"""/api/trade/* — bearer-token gate and DRY_RUN ceiling.

No network: get_broker is patched to a FakeBroker that records calls. What's
under test is that every trade route refuses without the dashboard token, and
that a request body can't arm a live order when the service is in DRY_RUN.
"""

import pytest
from flask import Flask

from app.api import register_blueprints

TOKEN = "test-token"
AUTH = {"Authorization": f"Bearer {TOKEN}"}
ORDER = {"symbol": "SPY", "side": "BUY", "quantity": 1}


class FakeBroker:
    def __init__(self):
        self.calls = []

    def submit_order(self, order):
        self.calls.append(("submit", order))
        return {"id": "o1", "symbol": order["symbol"], "status": "queued"}

    def cancel_order(self, order_id):
        self.calls.append(("cancel", order_id))

    def cancel_all(self):
        self.calls.append(("cancel_all",))


@pytest.fixture
def broker(monkeypatch):
    b = FakeBroker()
    monkeypatch.setattr("app.api.trade.get_broker", lambda name: b)
    return b


def make_client(dry_run=True, token=TOKEN):
    app = Flask(__name__)
    register_blueprints(app)
    app.config["DRY_RUN"] = dry_run
    app.config["DASHBOARD_REQUEST_TOKEN"] = token
    app.config["DEFAULT_BROKER"] = "robinhood"
    return app.test_client()


ROUTES = [
    ("post", "/api/trade/order/robinhood", ORDER),
    ("post", "/api/trade/buy/robinhood", {"symbol": "SPY", "quantity": 1}),
    ("post", "/api/trade/sell/robinhood", {"symbol": "SPY", "quantity": 1}),
    ("post", "/api/trade/cancel/abc/robinhood", None),
    ("post", "/api/trade/cancel-all/robinhood", None),
]


@pytest.mark.parametrize("method,path,body", ROUTES)
def test_routes_reject_without_token(broker, method, path, body):
    r = getattr(make_client(dry_run=False), method)(path, json=body)
    assert r.status_code == 401
    assert broker.calls == []


@pytest.mark.parametrize("method,path,body", ROUTES)
def test_routes_fail_closed_when_token_unconfigured(broker, method, path, body):
    r = getattr(make_client(dry_run=False, token=""), method)(
        path, json=body, headers=AUTH)
    assert r.status_code == 503
    assert broker.calls == []


def test_body_cannot_arm_live_order_when_service_dry_run(broker):
    r = make_client(dry_run=True).post(
        "/api/trade/order/robinhood", json={**ORDER, "dry_run": False}, headers=AUTH)
    assert r.status_code == 200 and r.get_json()["dry_run"] is True
    assert broker.calls == []


def test_body_can_still_request_dry_run_when_service_live(broker):
    r = make_client(dry_run=False).post(
        "/api/trade/order/robinhood", json={**ORDER, "dry_run": True}, headers=AUTH)
    assert r.get_json()["status"] == "simulated"
    assert broker.calls == []


def test_live_service_with_token_submits(broker):
    r = make_client(dry_run=False).post(
        "/api/trade/order/robinhood", json=ORDER, headers=AUTH)
    assert r.status_code == 201
    assert broker.calls[0][0] == "submit"
