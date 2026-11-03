"""Tests for the POST /options/order endpoint."""

from unittest.mock import MagicMock

import pytest

from app import create_app


@pytest.fixture
def client():
    app = create_app()
    app.config["TESTING"] = True
    app.config["DEFAULT_BROKER"] = "test"
    return app.test_client()


def _valid_body():
    return {
        "chain_symbol": "aapl",
        "option_type": "call",
        "strike": 150,
        "expiration": "2026-06-19",
        "side": "BUY",
        "quantity": 1,
        "limit_price": 2.50,
    }


def test_submit_option_order_success(client, monkeypatch):
    broker = MagicMock()
    broker.submit_option_order.return_value = {
        "id": "opt-1",
        "symbol": "AAPL",
        "status": "submitted",
    }
    monkeypatch.setattr("app.api.options.get_broker", lambda name: broker)

    resp = client.post("/api/options/order", json=_valid_body())
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["broker"] == "test"
    assert data["order"] == {"id": "opt-1", "symbol": "AAPL", "status": "submitted"}

    # broker received an upper-cased chain_symbol and defaulted order_type
    submitted = broker.submit_option_order.call_args[0][0]
    assert submitted["chain_symbol"] == "AAPL"
    assert submitted["order_type"] == "limit"


def test_submit_option_order_missing_fields(client, monkeypatch):
    broker = MagicMock()
    monkeypatch.setattr("app.api.options.get_broker", lambda name: broker)

    resp = client.post("/api/options/order", json={"chain_symbol": "AAPL"})
    assert resp.status_code == 400
    assert "error" in resp.get_json()
    broker.submit_option_order.assert_not_called()


def test_submit_option_order_no_body(client):
    resp = client.post("/api/options/order", json={})
    assert resp.status_code == 400
    assert "error" in resp.get_json()


def test_submit_option_order_unsupported_broker(client, monkeypatch):
    broker = MagicMock(spec=[])  # no submit_option_order attribute
    monkeypatch.setattr("app.api.options.get_broker", lambda name: broker)

    resp = client.post("/api/options/order", json=_valid_body())
    assert resp.status_code == 400
    assert "does not support option orders" in resp.get_json()["error"]


def test_submit_option_order_submission_failed(client, monkeypatch):
    broker = MagicMock()
    broker.submit_option_order.return_value = None
    monkeypatch.setattr("app.api.options.get_broker", lambda name: broker)

    resp = client.post("/api/options/order", json=_valid_body())
    assert resp.status_code == 502
    assert resp.get_json()["error"] == "Order submission failed"
