"""/api/risk/* and /risk — response contracts the Risk Desk page consumes."""

from datetime import datetime, timedelta, timezone

import pytest
from flask import Flask

from app.api import register_blueprints
import app.api.risk as risk_api

NOW = datetime(2026, 8, 20, tzinfo=timezone.utc)


class FakeBroker:
    def trade_fills(self):
        return [
            {"symbol": "AAA", "side": "BUY", "qty": 2, "price": 100.0, "ts": NOW - timedelta(days=10)},
            {"symbol": "BBB", "side": "SELL", "qty": 1, "price": 50.0, "ts": NOW - timedelta(days=10)},
        ]

    def price_history(self, symbol):
        base = {"AAA": [100.0, 104.0, 101.0, 106.0, 103.0, 108.0], "BBB": [50.0, 51.0, 49.0, 52.0, 50.0, 53.0]}[symbol]
        return [{"date": (NOW - timedelta(days=10 - i)).date().isoformat(), "close": c} for i, c in enumerate(base)]


class EmptyBroker:
    def trade_fills(self):
        return []

    def price_history(self, symbol):
        return []


class NoHistoryBroker:
    pass


@pytest.fixture
def client(monkeypatch):
    app = Flask(__name__)
    app.config["DEFAULT_BROKER"] = "fake"
    register_blueprints(app)
    brokers = {"fake": FakeBroker(), "empty": EmptyBroker(), "bare": NoHistoryBroker()}

    def fake_get_broker(name):
        if name not in brokers:
            raise ValueError(f"Unknown broker: {name}")
        return brokers[name]

    monkeypatch.setattr("app.api.risk.get_broker", fake_get_broker)
    risk_api._cache.clear()
    return app.test_client()


def test_report_has_every_section(client):
    res = client.get("/api/risk/report")
    assert res.status_code == 200
    body = res.get_json()
    assert body["broker"] == "fake"
    for key in ("headline", "flags", "performance", "drawdown", "exposure", "covariance",
                "tail", "stress", "symbols", "curve", "drawdownSeries", "rollingVol"):
        assert key in body, key
    assert body["headline"]["openPositions"] == 2
    assert body["headline"]["totalPnlUsd"] == pytest.approx(13.0)


def test_summary_is_headline_and_flags_only(client):
    body = client.get("/api/risk/summary").get_json()
    assert set(body) == {"broker", "generatedAt", "asOf", "headline", "flags"}


def test_section_routes_and_unknown_section(client):
    assert set(client.get("/api/risk/stress").get_json()["stress"]) == {"grossExposureUsd", "scenarios"}
    res = client.get("/api/risk/bogus")
    assert res.status_code == 404
    assert "sections" in res.get_json()


def test_report_is_cached_until_refresh(client):
    a = client.get("/api/risk/report").get_json()["generatedAt"]
    b = client.get("/api/risk/report").get_json()["generatedAt"]
    c = client.get("/api/risk/report?refresh=1").get_json()["generatedAt"]
    assert a == b
    assert c >= a


def test_empty_book_returns_empty_curve(client):
    body = client.get("/api/risk/report?broker=empty").get_json()
    assert body["curve"] == [] and body["headline"]["openPositions"] == 0


def test_unsupported_and_unknown_brokers_error_inline(client):
    assert client.get("/api/risk/report?broker=bare").status_code == 500
    res = client.get("/api/risk/report?broker=nope")
    assert res.status_code == 500 and "Unknown broker" in res.get_json()["error"]


def test_symbol_route(client):
    body = client.get("/api/risk/symbol/aaa").get_json()
    assert body["symbol"] == "AAA" and body["position"] == 2 and len(body["series"]) == 6


def test_risk_page_is_served():
    app = Flask(__name__)
    register_blueprints(app)
    res = app.test_client().get("/risk")
    assert res.status_code == 200 and b"Risk <span>Desk</span>" in res.data
