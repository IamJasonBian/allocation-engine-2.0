"""GET /api/orders/history — limit path and designated-symbol filter."""

from flask import Flask

from app.api import register_blueprints
from app.risk.equity_stop_mapping import EQUITY_STOP_MAPPING


class _Broker:
    def __init__(self, orders):
        self.orders = orders
        self.calls = []

    def order_history(self, limit=50, symbols=None):
        self.calls.append({"limit": limit, "symbols": symbols})
        rows = self.orders
        if symbols:
            rows = [o for o in rows if o.get("symbol") in symbols]
        return rows[:limit]


def _client(monkeypatch, orders):
    broker = _Broker(orders)
    monkeypatch.setattr("app.api.history.get_broker", lambda _name: broker)
    app = Flask(__name__)
    app.config["DEFAULT_BROKER"] = "robinhood"
    register_blueprints(app)
    return app.test_client(), broker


def test_history_filters_to_requested_symbols(monkeypatch):
    orders = [
        {"id": "1", "symbol": "NBIS", "type": "limit", "limit_price": 178.5},
        {"id": "2", "symbol": "SPY", "type": "limit", "limit_price": 744},
        {"id": "3", "symbol": "MU", "type": "limit", "limit_price": 96},
    ]
    client, broker = _client(monkeypatch, orders)
    r = client.get("/api/orders/history?limit=120&symbols=MU,NBIS")
    assert r.status_code == 200
    body = r.get_json()
    assert [o["symbol"] for o in body["orders"]] == ["NBIS", "MU"]
    assert broker.calls[-1] == {"limit": 120, "symbols": {"MU", "NBIS"}}


def test_history_limit_without_symbols_keeps_the_full_book(monkeypatch):
    orders = [{"id": "1", "symbol": "SPY"}, {"id": "2", "symbol": "NBIS"}]
    client, broker = _client(monkeypatch, orders)
    r = client.get("/api/orders/history?limit=50")
    assert [o["symbol"] for o in r.get_json()["orders"]] == ["SPY", "NBIS"]
    assert broker.calls[-1]["symbols"] is None


def test_coded_trails_are_mu_4_nbis_6():
    assert EQUITY_STOP_MAPPING["MU"]["trail"] == 4.0
    assert EQUITY_STOP_MAPPING["NBIS"]["trail"] == 6.0
