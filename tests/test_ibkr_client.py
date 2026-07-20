"""Offline tests for app/brokers/ibkr_client.py — mocks the CP Gateway's
REST responses so nothing here talks to a real gateway or the network.
"""

import pytest
import requests

from app.brokers import ibkr_client as ibkr_mod
from app.brokers.ibkr_client import IBKRTrader

BASE_URL = "https://gw:5000/v1/api"


@pytest.fixture(autouse=True)
def clear_conid_cache():
    ibkr_mod._conid_cache.clear()
    yield
    ibkr_mod._conid_cache.clear()


class FakeResponse:
    def __init__(self, payload, status=200):
        self._payload = payload
        self.status_code = status
        self.content = b"x" if payload is not None else b""

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(f"{self.status_code}")

    def json(self):
        return self._payload


def make_trader(router: dict, account_id="U123"):
    """router maps (method, path) -> payload returned verbatim for every
    call to that key. Each test's paths are distinct enough (submit vs.
    reply, etc.) that no per-call sequencing is needed."""
    calls = []

    def fake_request(method, url, **kwargs):
        path = url[len(BASE_URL):]
        calls.append((method, path, kwargs))
        key = (method, path)
        if key not in router:
            raise AssertionError(f"unexpected request {key}")
        return FakeResponse(router[key])

    trader = IBKRTrader(gateway_url=BASE_URL, account_id=account_id, verify_ssl=False, timeout=5)
    trader._session.request = fake_request
    trader._calls = calls
    return trader


def _auth_ok(extra=None):
    return {
        ("GET", "/iserver/auth/status"): {"authenticated": True, "connected": True},
        ("POST", "/tickle"): {},
        **(extra or {}),
    }


def test_ensure_auth_raises_when_gateway_not_logged_in():
    router = {("GET", "/iserver/auth/status"): {"authenticated": False, "connected": False}}
    trader = make_trader(router)
    with pytest.raises(RuntimeError, match="not authenticated"):
        trader._ensure_auth()


def test_account_maps_summary_fields():
    router = _auth_ok({
        ("GET", "/portfolio/U123/summary"): {
            "netliquidation": {"amount": 10000.5},
            "totalcashvalue": {"amount": 2000.0},
            "buyingpower": {"amount": 4000.0},
        },
    })
    trader = make_trader(router)
    acct = trader.account()
    assert acct == {
        "equity": 10000.5, "cash": 2000.0, "buying_power": 4000.0, "portfolio_value": 10000.5,
    }


def test_positions_skips_zero_qty_and_computes_pl():
    router = _auth_ok({
        ("GET", "/portfolio/U123/positions/0"): [
            {"ticker": "AAPL", "position": 10, "avgCost": 100.0, "mktPrice": 150.0, "mktValue": 1500.0},
            {"ticker": "MSFT", "position": 0, "avgCost": 50.0},
        ],
    })
    trader = make_trader(router)
    positions = trader.positions()
    assert len(positions) == 1
    p = positions[0]
    assert p["symbol"] == "AAPL"
    assert p["qty"] == 10
    assert p["market_value"] == 1500.0
    assert p["unrealized_pl"] == 500.0


def test_open_orders_filters_to_open_states():
    router = _auth_ok({
        ("GET", "/iserver/account/orders"): {"orders": [
            {"orderId": 111, "ticker": "AAPL", "side": "BUY", "remainingQuantity": 5,
             "orderType": "LMT", "price": 150.0, "status": "Submitted"},
            {"orderId": 222, "ticker": "MSFT", "side": "SELL", "remainingQuantity": 0,
             "orderType": "MKT", "status": "Filled"},
        ]},
    })
    trader = make_trader(router)
    orders = trader.open_orders()
    assert len(orders) == 1
    assert orders[0]["id"] == "111"
    assert orders[0]["type"] == "limit"
    assert orders[0]["limit_price"] == 150.0


def test_submit_order_market_resolves_conid_and_submits():
    router = _auth_ok({
        ("GET", "/iserver/secdef/search"): [{"conid": 265598, "secType": "STK"}],
        ("POST", "/iserver/account/U123/orders"): [{"order_id": "999", "order_status": "Submitted"}],
    })
    trader = make_trader(router)
    result = trader.submit_order({"symbol": "AAPL", "side": "BUY", "quantity": 5})
    assert result == {"id": "999", "symbol": "AAPL", "status": "Submitted"}


def test_submit_order_auto_confirms_precaution_questions():
    router = _auth_ok({
        ("GET", "/iserver/secdef/search"): [{"conid": 265598, "secType": "STK"}],
        ("POST", "/iserver/account/U123/orders"): [{"id": "q1", "message": ["order value warning"]}],
        ("POST", "/iserver/reply/q1"): [{"order_id": "999", "order_status": "Submitted"}],
    })
    trader = make_trader(router)
    result = trader.submit_order({
        "symbol": "AAPL", "side": "BUY", "quantity": 5,
        "order_type": "limit", "limit_price": 150.0,
    })
    assert result == {"id": "999", "symbol": "AAPL", "status": "Submitted"}


def test_submit_order_unknown_symbol_returns_none():
    router = _auth_ok({("GET", "/iserver/secdef/search"): []})
    trader = make_trader(router)
    result = trader.submit_order({"symbol": "ZZZZ", "side": "BUY", "quantity": 1})
    assert result is None


def test_cancel_all_cancels_each_open_order():
    router = _auth_ok({
        ("GET", "/iserver/account/orders"): {"orders": [
            {"orderId": 111, "ticker": "AAPL", "side": "BUY", "remainingQuantity": 5,
             "orderType": "MKT", "status": "Submitted"},
            {"orderId": 222, "ticker": "MSFT", "side": "SELL", "remainingQuantity": 3,
             "orderType": "MKT", "status": "PreSubmitted"},
        ]},
        ("DELETE", "/iserver/account/U123/order/111"): {},
        ("DELETE", "/iserver/account/U123/order/222"): {},
    })
    trader = make_trader(router)
    trader.cancel_all()
    deletes = [c for c in trader._calls if c[0] == "DELETE"]
    assert {c[1] for c in deletes} == {
        "/iserver/account/U123/order/111", "/iserver/account/U123/order/222",
    }


def test_funding_methods_are_not_implemented():
    trader = make_trader(_auth_ok())
    with pytest.raises(NotImplementedError):
        trader.deposit(100)
    with pytest.raises(NotImplementedError):
        trader.withdraw(100)
    with pytest.raises(NotImplementedError):
        trader.linked_bank_accounts()
    with pytest.raises(NotImplementedError):
        trader.transfer_history()
