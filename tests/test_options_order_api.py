"""Tests for the POST /options/order endpoint (broker-agnostic)."""

from unittest.mock import MagicMock

import pytest

import app.api.options as options_module
from app import create_app
from app.enums import OrderSide


@pytest.fixture
def client():
    app = create_app()
    app.config["TESTING"] = True
    app.config["DEFAULT_BROKER"] = "robinhood"
    with app.test_client() as c:
        yield c


def _valid_body(**overrides):
    body = {
        "chain_symbol": "aapl",
        "option_type": "call",
        "strike": 190,
        "expiration": "2026-06-19",
        "side": "buy",
        "quantity": 1,
    }
    body.update(overrides)
    return body


def _mock_broker(submit_return):
    broker = MagicMock()
    broker.submit_option_order.return_value = submit_return
    return broker


def test_places_order_and_returns_200(client, monkeypatch):
    canned = {"id": "opt-123", "symbol": "AAPL 2026-06-19 C190", "status": "queued"}
    broker = _mock_broker(canned)
    monkeypatch.setattr(options_module, "get_broker", lambda name: broker)

    resp = client.post("/api/options/order", json=_valid_body())

    assert resp.status_code == 200
    data = resp.get_json()
    assert data["broker"] == "robinhood"
    assert data["order"] == canned

    # broker was called once with a normalized order dict
    broker.submit_option_order.assert_called_once()
    order = broker.submit_option_order.call_args.args[0]
    assert order["chain_symbol"] == "AAPL"
    assert order["side"] == OrderSide.BUY
    assert order["strike"] == 190.0
    assert order["quantity"] == 1.0
    # optional peg/cancel fields not provided → absent
    assert "peg_delta" not in order
    assert "cancel_if_underlying_above" not in order
    assert "cancel_if_underlying_below" not in order
    assert "limit_price" not in order


def test_forwards_optional_peg_and_cancel_fields(client, monkeypatch):
    broker = _mock_broker({"id": "x", "symbol": "Y", "status": "queued"})
    monkeypatch.setattr(options_module, "get_broker", lambda name: broker)

    body = _valid_body(
        limit_price=2.50,
        peg_delta=0.05,
        cancel_if_underlying_above=200,
        cancel_if_underlying_below=150,
    )
    resp = client.post("/api/options/order", json=body)

    assert resp.status_code == 200
    order = broker.submit_option_order.call_args.args[0]
    assert order["limit_price"] == 2.50
    assert order["peg_delta"] == 0.05
    assert order["cancel_if_underlying_above"] == 200
    assert order["cancel_if_underlying_below"] == 150


def test_missing_required_fields_returns_400(client, monkeypatch):
    broker = _mock_broker({"id": "x"})
    monkeypatch.setattr(options_module, "get_broker", lambda name: broker)

    resp = client.post("/api/options/order", json={"chain_symbol": "AAPL"})

    assert resp.status_code == 400
    data = resp.get_json()
    assert data["error"] == "Validation failed"
    assert isinstance(data["details"], list)
    assert len(data["details"]) >= 1
    broker.submit_option_order.assert_not_called()


def test_unsupported_broker_returns_400(client, monkeypatch):
    broker = MagicMock(spec=[])  # no submit_option_order attribute
    monkeypatch.setattr(options_module, "get_broker", lambda name: broker)

    resp = client.post("/api/options/order/robinhood", json=_valid_body())

    assert resp.status_code == 400
    assert "does not support option orders" in resp.get_json()["error"]


def test_none_result_returns_502(client, monkeypatch):
    broker = _mock_broker(None)
    monkeypatch.setattr(options_module, "get_broker", lambda name: broker)

    resp = client.post("/api/options/order", json=_valid_body())

    assert resp.status_code == 502
    assert resp.get_json()["error"] == "Order submission failed"
