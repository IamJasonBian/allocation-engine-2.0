"""Offline tests for app/box_session.py — sqlite-first token cache.

Also asserts the architectural invariant: the app never runs Robinhood
authentication itself; robinhood_client contains no rh.login() call.
"""

import json
import time

import pytest

from app import box_session as bs


class FakeAuthClient:
    def __init__(self, tok=None, error=None):
        self.tok = tok
        self.error = error
        self.calls = 0

    def get_token(self):
        self.calls += 1
        if self.error:
            raise self.error
        return dict(self.tok)


def fresh_token(expires_in=3600):
    return {"token": "acc-123", "token_type": "Bearer",
            "expires_at": time.time() + expires_in, "account_number": "A1"}


@pytest.fixture
def db(tmp_path):
    return str(tmp_path / "queue.sqlite3")


# --------------------------------------------------------------------------- #
# cache semantics — sqlite first, box only when stale
# --------------------------------------------------------------------------- #

def test_first_fetch_hits_box_and_caches(db):
    c = FakeAuthClient(tok=fresh_token())
    tok = bs.get_box_token(client=c, db_path=db)
    assert tok["token"] == "acc-123"
    assert c.calls == 1
    assert bs.get_cached_token(db)["token"] == "acc-123"


def test_cached_token_skips_box(db):
    c = FakeAuthClient(tok=fresh_token())
    bs.get_box_token(client=c, db_path=db)
    tok = bs.get_box_token(client=c, db_path=db)   # second call
    assert tok["token"] == "acc-123"
    assert c.calls == 1, "fresh cached token must not touch the box"


def test_expiring_token_refetches(db):
    c = FakeAuthClient(tok=fresh_token())
    bs._write_meta(db, bs._META_KEY, json.dumps(fresh_token(expires_in=60)))
    tok = bs.get_box_token(client=c, db_path=db)   # within 5-min lead window
    assert c.calls == 1
    assert tok["token"] == "acc-123"


def test_force_refetches_despite_fresh_cache(db):
    c = FakeAuthClient(tok=fresh_token())
    bs.get_box_token(client=c, db_path=db)
    bs.get_box_token(client=c, db_path=db, force=True)
    assert c.calls == 2


def test_box_error_returns_none_never_raises(db):
    from app.auth_service_client import AuthServiceError
    c = FakeAuthClient(error=AuthServiceError("auth-service GET /token returned 404"))
    assert bs.get_box_token(client=c, db_path=db) is None


def test_otp_pending_returns_none(db):
    from app.auth_service_client import OTPRequired
    c = FakeAuthClient(error=OTPRequired("device approval needed"))
    assert bs.get_box_token(client=c, db_path=db) is None


def test_iso_and_epoch_expiry_both_parse():
    assert bs.token_expiring({"expires_at": time.time() + 3600}) is False
    assert bs.token_expiring({"expires_at": time.time() + 10}) is True
    iso_future = "2099-01-01T00:00:00+00:00"
    assert bs.token_expiring({"expires_at": iso_future}) is False
    assert bs.token_expiring({"expires_at": "garbage"}) is True
    assert bs.token_expiring({}) is True


def test_cached_token_status_fragment(db):
    assert bs.cached_token_status(db) == {"box_token_cached": False}
    bs._write_meta(db, bs._META_KEY, json.dumps(fresh_token()))
    st = bs.cached_token_status(db)
    assert st["box_token_cached"] is True
    assert st["account_number"] == "A1"
    assert st["box_token_expiring"] is False


# --------------------------------------------------------------------------- #
# the invariant: no local Robinhood authentication, anywhere in the app
# --------------------------------------------------------------------------- #

def test_app_never_calls_rh_login():
    import os
    root = os.path.join(os.path.dirname(__file__), "..", "app")
    offenders = []
    for dirpath, _, files in os.walk(root):
        for f in files:
            if not f.endswith(".py"):
                continue
            path = os.path.join(dirpath, f)
            for i, line in enumerate(open(path), 1):
                code = line.split("#")[0]
                if "rh.login(" in code or "robinhood.authenticate(" in code:
                    offenders.append(f"{path}:{i}")
    assert not offenders, f"local Robinhood auth is forbidden: {offenders}"
