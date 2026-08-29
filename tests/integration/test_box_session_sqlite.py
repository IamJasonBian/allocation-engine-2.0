"""Integration: box-token cache against a real sqlite file, no network.

Only the box itself is stubbed (a tiny object with get_token); everything
between the engine and disk — json encoding, the meta table, expiry math —
runs for real.
"""

import time

import pytest

from app import box_session

pytestmark = pytest.mark.integration


class _Box:
    def __init__(self, expires_in=3600):
        self.calls = 0
        self.expires_in = expires_in

    def get_token(self):
        self.calls += 1
        return {
            "token": f"tok-{self.calls}",
            "token_type": "Bearer",
            "expires_at": time.time() + self.expires_in,
            "account_number": "494636921",
        }


def test_second_call_is_served_from_sqlite(tmp_path):
    db = tmp_path / "engine.sqlite"
    box = _Box()
    first = box_session.get_box_token(client=box, db_path=db)
    second = box_session.get_box_token(client=box, db_path=db)
    assert first["token"] == second["token"] == "tok-1"
    assert box.calls == 1
    assert db.exists()
    assert box_session.get_cached_token(db)["account_number"] == "494636921"


def test_expiring_token_is_refetched(tmp_path):
    db = tmp_path / "engine.sqlite"
    box = _Box(expires_in=1)  # inside the expiry lead window
    box_session.get_box_token(client=box, db_path=db)
    again = box_session.get_box_token(client=box, db_path=db)
    assert again["token"] == "tok-2"
    assert box.calls == 2


def test_force_bypasses_cache(tmp_path):
    db = tmp_path / "engine.sqlite"
    box = _Box()
    box_session.get_box_token(client=box, db_path=db)
    forced = box_session.get_box_token(force=True, client=box, db_path=db)
    assert forced["token"] == "tok-2"
    assert box_session.get_cached_token(db)["token"] == "tok-2"
