"""The /api/trade/* routes place and cancel live orders: they must require the token."""

import pytest
from flask import Flask

from app.api import register_blueprints

TOKEN = "t0ken"
AUTH = {"Authorization": f"Bearer {TOKEN}"}

ROUTES = [
    ("post", "/api/trade/order", {"symbol": "AAPL", "side": "BUY", "quantity": 1}),
    ("post", "/api/trade/buy", {"symbol": "AAPL", "quantity": 1}),
    ("post", "/api/trade/sell", {"symbol": "AAPL", "quantity": 1}),
    ("post", "/api/trade/cancel/abc", {}),
    ("delete", "/api/trade/cancel-all", {}),
]


def _client(token):
    app = Flask(__name__)
    register_blueprints(app)
    app.config.update(DRY_RUN=True, DEFAULT_BROKER="robinhood",
                      DASHBOARD_REQUEST_TOKEN=token)
    return app.test_client()


@pytest.mark.parametrize("method,path,body", ROUTES)
def test_trade_routes_reject_missing_token(method, path, body):
    r = getattr(_client(TOKEN), method)(path, json=body)
    assert r.status_code == 401


@pytest.mark.parametrize("method,path,body", ROUTES)
def test_trade_routes_reject_wrong_token(method, path, body):
    r = getattr(_client(TOKEN), method)(path, json=body,
                                        headers={"Authorization": "Bearer nope"})
    assert r.status_code == 401


@pytest.mark.parametrize("method,path,body", ROUTES)
def test_trade_routes_fail_closed_when_token_unset(method, path, body):
    r = getattr(_client(""), method)(path, json=body, headers=AUTH)
    assert r.status_code == 503


def test_authorized_order_still_dry_runs():
    r = _client(TOKEN).post("/api/trade/order", headers=AUTH,
                            json={"symbol": "AAPL", "side": "BUY", "quantity": 1})
    body = r.get_json()
    assert r.status_code == 200 and body["dry_run"] is True
    assert body["status"] == "simulated"
