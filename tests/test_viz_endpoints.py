"""The /api/viz/* endpoints — drift, order-funnel, wheel-lanes chart contracts.

Brokers are faked at the module boundary (each endpoint imported get_broker
into its own namespace); what's under test is the response shape each
visualization consumes.
"""

import pytest
from flask import Flask

from app.api import register_blueprints


class FakeIBKR:
    """Quacks like IBKRTrader's read surface."""

    def positions(self):
        return [
            {"symbol": "IWN", "qty": 10.0, "side": "long", "market_value": 1200.0,
             "avg_entry": 100.0, "unrealized_pl": 200.0, "unrealized_pl_pct": 0.02},
            {"symbol": "AMD", "qty": 100.0, "side": "long", "market_value": 9700.0,
             "avg_entry": 95.0, "unrealized_pl": -500.0, "unrealized_pl_pct": -0.05},
        ]

    def session_trades(self):
        return [
            {"id": "11", "symbol": "SPY", "sec_type": "OPT", "side": "sell",
             "qty": 1.0, "order_type": "limit", "limit_price": 1.25,
             "status": "Filled", "filled_qty": 1.0, "avg_fill_price": 1.27},
            {"id": "12", "symbol": "AMD", "sec_type": "STK", "side": "buy",
             "qty": 5.0, "order_type": "market", "limit_price": None,
             "status": "Submitted", "filled_qty": 0.0, "avg_fill_price": None},
        ]

    def executions(self):
        return [
            {"exec_id": "e1", "order_id": "11", "symbol": "SPY", "sec_type": "OPT",
             "side": "sell", "qty": 1.0, "price": 1.27, "time": "2026-08-09 14:30:00"},
        ]

    def portfolio_detail(self):
        return [
            {"symbol": "AMD", "sec_type": "STK", "right": None, "strike": None,
             "expiry": None, "qty": 100.0, "avg_cost": 95.0,
             "market_value": 9700.0, "unrealized_pl": 200.0},
            {"symbol": "AMD", "sec_type": "OPT", "right": "C", "strike": 105.0,
             "expiry": "20260918", "qty": -1.0, "avg_cost": 180.0,
             "market_value": -120.0, "unrealized_pl": 60.0},
            {"symbol": "QQQ", "sec_type": "OPT", "right": "P", "strike": 415.0,
             "expiry": "20260821", "qty": -1.0, "avg_cost": 210.0,
             "market_value": -190.0, "unrealized_pl": 20.0},
        ]


class FakeMinimalBroker:
    """A broker without history/option detail (alpaca/robinhood shape)."""

    def positions(self):
        return []


@pytest.fixture
def client(monkeypatch):
    app = Flask(__name__)
    register_blueprints(app)

    def fake_get_broker(name):
        return FakeMinimalBroker() if name == "minimal" else FakeIBKR()

    for mod in ("drift", "order_funnel", "wheel_lanes"):
        monkeypatch.setattr(f"app.api.{mod}.get_broker", fake_get_broker)
    return app.test_client()


def test_drift_reports_worst_symbol_and_rail(client):
    body = client.get("/api/viz/drift").get_json()
    assert body["broker"] == "ibkr"
    assert body["soft_band"] == 0.08
    assert body["hard_band"] is None
    assert body["max_symbol"] == "AMD"
    assert body["max_relative_drift"] == pytest.approx(0.05)
    assert {s["symbol"] for s in body["symbols"]} == {"IWN", "AMD"}


def test_order_funnel_stages_and_slippage(client):
    body = client.get("/api/viz/order-funnel").get_json()
    assert body["stages"] == {"placed": 2, "filled": 1}
    (fill,) = body["fills"]
    assert fill["slippage"] == pytest.approx(0.02)
    assert fill["favorable"] is True  # sold above limit
    assert body["executions"][0]["exec_id"] == "e1"


def test_wheel_lanes_groups_and_phases(client):
    body = client.get("/api/viz/wheel-lanes").get_json()
    lanes = {lane["symbol"]: lane for lane in body["lanes"]}
    assert lanes["AMD"]["phase"] == "covered_call"
    assert lanes["AMD"]["shares"]["qty"] == 100.0
    assert lanes["AMD"]["options"][0]["right"] == "C"
    assert lanes["QQQ"]["phase"] == "cash_secured_put"
    assert lanes["QQQ"]["shares"] is None

