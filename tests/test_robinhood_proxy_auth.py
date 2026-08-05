"""Routes that can reach the broker must never be open.

This service attaches a live Robinhood bearer server-side, so an
unauthenticated route that reaches the broker is an open relay: the caller
supplies no credentials of their own and still trades the account.

The worst of these needs no flag at all — `/api/trade/cancel-all` has no
dry_run check anywhere in its handler, so an anonymous request cancels every
open order, including the protective trailing stops.
"""

import pytest

from app import create_app
from app.config import Config

TOKEN = "proxy-secret"


def _client(token=TOKEN):
    class TestConfig(Config):
        TESTING = True
        RH_PROXY_TOKEN = token

    return create_app(TestConfig).test_client()


# Everything that reaches the broker with our credentials.
GUARDED = [
    ("get", "/api/robinhood/trailing-stop"),
    ("post", "/api/robinhood/trailing-stop"),
    ("post", "/api/robinhood/mcp"),
    ("post", "/api/trade/order"),
    ("post", "/api/trade/buy"),
    ("post", "/api/trade/sell"),
    ("post", "/api/trade/cancel"),
    ("delete", "/api/trade/cancel"),
    ("post", "/api/trade/cancel-all"),
    ("delete", "/api/trade/cancel-all"),
    ("post", "/api/engine/tick"),
    ("post", "/api/claude/reauth"),
]


@pytest.mark.parametrize("method,path", GUARDED)
def test_no_credentials_is_rejected(method, path):
    assert getattr(_client(), method)(path, json={}).status_code == 401


@pytest.mark.parametrize("method,path", GUARDED)
def test_wrong_token_is_rejected(method, path):
    resp = getattr(_client(), method)(
        path, json={}, headers={"Authorization": "Bearer nope"})
    assert resp.status_code == 401


@pytest.mark.parametrize("method,path", GUARDED)
def test_unset_token_fails_closed(method, path):
    # An unconfigured gate must shut the door, not leave it open.
    resp = getattr(_client(token=""), method)(
        path, json={}, headers={"Authorization": f"Bearer {TOKEN}"})
    assert resp.status_code == 503


def test_cancel_all_needs_the_token_even_though_it_has_no_dry_run():
    # There is no dry_run check in this handler at all — the token is the
    # only thing between an anonymous request and a wiped order book.
    assert _client().post("/api/trade/cancel-all").status_code == 401


def test_a_live_sell_still_needs_the_token():
    body = {"symbol": "AAPL", "side": "SELL", "quantity": 1000,
            "order_type": "market", "dry_run": False}
    assert _client().post("/api/trade/order", json=body).status_code == 401


def test_read_only_endpoints_stay_open():
    # Health and status are read-only and dashboards depend on them.
    client = _client()
    for path in ("/api/health", "/api/engine/status"):
        assert client.get(path).status_code != 401


def test_valid_token_passes_the_gate():
    # Past the gate it reaches the handler, which fails on missing
    # auth-service config rather than being turned away at the door.
    resp = _client().get("/api/robinhood/trailing-stop",
                         headers={"Authorization": f"Bearer {TOKEN}"})
    assert resp.status_code != 401


def test_x_api_key_header_is_accepted():
    resp = _client().get("/api/robinhood/trailing-stop",
                         headers={"X-Api-Key": TOKEN})
    assert resp.status_code != 401
