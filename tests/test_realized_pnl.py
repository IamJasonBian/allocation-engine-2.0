"""RobinhoodTrader.realized_pnl — full-history replay, windowed reporting."""

from datetime import datetime, timedelta, timezone

import pytest

import app.brokers.robinhood_client as rc

NOW = datetime.now(timezone.utc)


def _order(symbol, side, qty, price, days_ago, state="filled"):
    ts = (NOW - timedelta(days=days_ago)).isoformat().replace("+00:00", "Z")
    return {
        "instrument": symbol,  # resolved 1:1 by the patched symbol lookup
        "side": side,
        "state": state,
        "cumulative_quantity": str(qty),
        "average_price": str(price),
        "created_at": ts,
        "updated_at": ts,
    }


@pytest.fixture
def trader(monkeypatch):
    monkeypatch.setattr(rc.RobinhoodTrader, "_ensure_auth", lambda self: None)
    monkeypatch.setattr(rc, "_symbol_from_instrument", lambda url: url)
    return rc.RobinhoodTrader()


def _patch_orders(monkeypatch, orders):
    monkeypatch.setattr(rc.rh.orders, "get_all_stock_orders", lambda: orders)


def test_basis_before_window_counted(trader, monkeypatch):
    # Old implementation dropped the buy (outside window) and reported 0.
    _patch_orders(monkeypatch, [
        _order("AAA", "buy", 100, 10.0, days_ago=90),
        _order("AAA", "sell", 50, 20.0, days_ago=3),
    ])
    out = trader.realized_pnl(days=30)
    assert out["totalRealizedPnL"] == pytest.approx(500.0)
    sym = out["symbols"][0]
    assert sym["symbol"] == "AAA"
    assert sym["realizedPnL"] == pytest.approx(500.0)
    assert sym["netPosition"] == pytest.approx(50)
    assert sym["avgOpenPrice"] == pytest.approx(10.0)


def test_partial_fill_on_cancelled_order_counts(trader, monkeypatch):
    _patch_orders(monkeypatch, [
        _order("BBB", "buy", 10, 10.0, days_ago=5),
        _order("BBB", "sell", 4, 15.0, days_ago=2, state="cancelled"),
    ])
    out = trader.realized_pnl(days=30)
    assert out["totalRealizedPnL"] == pytest.approx(20.0)


def test_unfilled_and_malformed_orders_skipped(trader, monkeypatch):
    _patch_orders(monkeypatch, [
        {"side": "buy", "cumulative_quantity": "0", "average_price": None,
         "state": "cancelled", "instrument": "CCC", "created_at": ""},
        "not-a-dict",
        _order("CCC", "buy", 5, 10.0, days_ago=1),
    ])
    out = trader.realized_pnl(days=30)
    assert out["totalRealizedPnL"] == 0.0
    assert out["symbols"][0]["buyCount"] == 1


def test_realized_outside_window_excluded(trader, monkeypatch):
    _patch_orders(monkeypatch, [
        _order("DDD", "buy", 10, 10.0, days_ago=100),
        _order("DDD", "sell", 10, 20.0, days_ago=60),  # realized, but old
    ])
    out = trader.realized_pnl(days=30)
    assert out["totalRealizedPnL"] == 0.0
    assert out["symbols"] == []
