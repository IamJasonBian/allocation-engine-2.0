"""Offline tests for app/brokers/ibkr_client.py — no network.

_get/_post/_delete are monkeypatched per-test so we can assert on the CPAPI
payloads we build and drive the order-confirmation-reply flow, without a
real Client Portal Gateway.
"""

import pytest

from app.brokers.ibkr_client import IBKRTrader
from app.enums import OrderSide, OrderType


@pytest.fixture
def trader():
    return IBKRTrader(base_url="https://gw.example:5000/v1/api", account_id="U123")


# --------------------------------------------------------------------------- #
# conid resolution
# --------------------------------------------------------------------------- #

def test_resolve_conid_matches_stock_section(trader, monkeypatch):
    def fake_get(path, params=None):
        assert path == "/iserver/secdef/search"
        assert params == {"symbol": "AAPL"}
        return [
            {"symbol": "AAPL", "conid": 265598, "sections": [{"secType": "STK"}]},
            {"symbol": "AAPL", "conid": 999, "sections": [{"secType": "OPT"}]},
        ]

    monkeypatch.setattr(trader, "_get", fake_get)
    assert trader._resolve_conid("AAPL") == 265598
    # cached on second call — _get would fail the assert above if hit again
    monkeypatch.setattr(trader, "_get", lambda *a, **k: (_ for _ in ()).throw(AssertionError("no cache")))
    assert trader._resolve_conid("AAPL") == 265598


def test_resolve_conid_no_match_returns_none(trader, monkeypatch):
    monkeypatch.setattr(trader, "_get", lambda *a, **k: [])
    assert trader._resolve_conid("ZZZZ") is None


# --------------------------------------------------------------------------- #
# order submission — including the confirm-reply chain
# --------------------------------------------------------------------------- #

def test_submit_market_order_confirms_replies(trader, monkeypatch):
    monkeypatch.setattr(trader, "_tickle", lambda: None)
    monkeypatch.setattr(trader, "_resolve_conid", lambda symbol: 265598)

    posts = []

    def fake_post(path, payload):
        posts.append((path, payload))
        if path == "/iserver/account/U123/orders":
            assert payload == {
                "orders": [{
                    "conid": 265598, "orderType": "MKT", "side": "BUY",
                    "quantity": 5.0, "tif": "GTC", "acctId": "U123",
                }]
            }
            return [{"id": "reply-1", "message": ["risk warning"]}]
        if path == "/iserver/reply/reply-1":
            assert payload == {"confirmed": True}
            return [{"order_id": "999", "order_status": "submitted"}]
        raise AssertionError(f"unexpected post to {path}")

    monkeypatch.setattr(trader, "_post", fake_post)

    result = trader.submit_order({
        "symbol": "AAPL", "side": OrderSide.BUY, "quantity": 5,
        "order_type": OrderType.MARKET,
    })

    assert result == {"id": "999", "symbol": "AAPL", "status": "submitted"}
    assert len(posts) == 2


def test_submit_limit_order_sets_price(trader, monkeypatch):
    monkeypatch.setattr(trader, "_tickle", lambda: None)
    monkeypatch.setattr(trader, "_resolve_conid", lambda symbol: 42)

    def fake_post(path, payload):
        assert payload["orders"][0]["orderType"] == "LMT"
        assert payload["orders"][0]["price"] == 123.45
        return [{"order_id": "1", "order_status": "submitted"}]

    monkeypatch.setattr(trader, "_post", fake_post)

    result = trader.submit_order({
        "symbol": "SPY", "side": OrderSide.SELL, "quantity": 1,
        "order_type": OrderType.LIMIT, "limit_price": 123.45,
    })
    assert result["id"] == "1"


def test_submit_order_unresolved_symbol_returns_none(trader, monkeypatch):
    monkeypatch.setattr(trader, "_tickle", lambda: None)
    monkeypatch.setattr(trader, "_resolve_conid", lambda symbol: None)
    result = trader.submit_order({
        "symbol": "ZZZZ", "side": OrderSide.BUY, "quantity": 1,
    })
    assert result is None


# --------------------------------------------------------------------------- #
# positions / account parsing
# --------------------------------------------------------------------------- #

def test_positions_skips_zero_qty_and_computes_pl_pct(trader, monkeypatch):
    monkeypatch.setattr(trader, "_tickle", lambda: None)
    monkeypatch.setattr(trader, "_get", lambda *a, **k: [
        {"ticker": "AAPL", "position": 10, "avgCost": 100, "mktValue": 1100, "unrealizedPnl": 100},
        {"ticker": "MSFT", "position": 0, "avgCost": 200, "mktValue": 0, "unrealizedPnl": 0},
    ])
    positions = trader.positions()
    assert len(positions) == 1
    assert positions[0]["symbol"] == "AAPL"
    assert positions[0]["unrealized_pl_pct"] == pytest.approx(0.1)


def test_account_reads_summary_amounts(trader, monkeypatch):
    monkeypatch.setattr(trader, "_tickle", lambda: None)
    monkeypatch.setattr(trader, "_get", lambda *a, **k: {
        "netliquidation": {"amount": 50000},
        "totalcashvalue": {"amount": 10000},
        "buyingpower": {"amount": 20000},
    })
    account = trader.account()
    assert account == {
        "equity": 50000.0, "cash": 10000.0,
        "buying_power": 20000.0, "portfolio_value": 50000.0,
    }


# --------------------------------------------------------------------------- #
# funding — no retail API exists, must fail loudly rather than pretend
# --------------------------------------------------------------------------- #

def test_deposit_and_withdraw_are_not_implemented(trader):
    with pytest.raises(NotImplementedError):
        trader.deposit(100)
    with pytest.raises(NotImplementedError):
        trader.withdraw(100)
