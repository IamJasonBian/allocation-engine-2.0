"""Quote mid (engine estimate) and the /api/earnings windows."""

from types import SimpleNamespace

import pytest
from flask import Flask

from app.api import register_blueprints
from app.brokers.alpaca_client import _quote_mid


def test_quote_mid_is_halfway_between_bid_and_ask():
    assert _quote_mid(SimpleNamespace(bid_price=144.30, ask_price=194.32)) == 169.31


def test_quote_mid_falls_back_to_the_present_side():
    assert _quote_mid(SimpleNamespace(bid_price=0, ask_price=10.5)) == 10.5
    assert _quote_mid(SimpleNamespace(bid_price=9.5, ask_price=None)) == 9.5
    assert _quote_mid(SimpleNamespace(bid_price=None, ask_price=None)) == 0


@pytest.fixture
def client():
    app = Flask(__name__)
    register_blueprints(app)
    return app.test_client()


def test_earnings_lists_windows_filtered_by_symbol(client):
    r = client.get("/api/earnings?symbols=nbis")
    assert r.status_code == 200
    windows = r.get_json()["windows"]
    assert windows and all(w["symbol"] == "NBIS" for w in windows)
    assert all(w["start_time"] < w["end_time"] for w in windows)


def test_earnings_without_filter_returns_every_window(client):
    from app.overrides import CUSTOM_OVERRIDES
    assert len(client.get("/api/earnings").get_json()["windows"]) == len(CUSTOM_OVERRIDES)
