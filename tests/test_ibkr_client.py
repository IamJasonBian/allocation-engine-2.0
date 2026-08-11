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
# transport — tickle is centralized in _get/_post/_delete, not sprinkled at
# each call site, so every real CPAPI request (including ones made deep
# inside submit_order's conid resolution / reply confirmation) gets one.
# --------------------------------------------------------------------------- #

def test_get_tickles_before_the_real_request(trader, monkeypatch):
    calls = []
    monkeypatch.setattr(trader, "_tickle", lambda: calls.append("tickle"))

    class FakeResponse:
        content = b"{}"
        def raise_for_status(self):
            pass
        def json(self):
            calls.append("request")
            return {}

    monkeypatch.setattr("app.brokers.ibkr_client.requests.get", lambda *a, **k: FakeResponse())
    trader._get("/some/path")
    assert calls == ["tickle", "request"]


def test_tickle_itself_does_not_recurse(trader, monkeypatch):
    """_tickle() must not call itself via _post's default tickle=True."""
    posts = []

    def fake_requests_post(url, json=None, verify=None, timeout=None):
        posts.append(json)
        class FakeResponse:
            content = b""
            def raise_for_status(self):
                pass
        return FakeResponse()

    monkeypatch.setattr("app.brokers.ibkr_client.requests.post", fake_requests_post)
    trader._tickle()
    assert len(posts) == 1  # exactly one /tickle POST, no recursive re-tickle


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


def test_submit_stop_order_uses_stp_and_price_field(trader, monkeypatch):
    """CPAPI's STP type carries the stop price in `price`, not `auxPrice`."""
    monkeypatch.setattr(trader, "_tickle", lambda: None)
    monkeypatch.setattr(trader, "_resolve_conid", lambda symbol: 42)

    def fake_post(path, payload):
        order = payload["orders"][0]
        assert order["orderType"] == "STP"
        assert order["price"] == 99.5
        assert "auxPrice" not in order
        return [{"order_id": "2", "order_status": "submitted"}]

    monkeypatch.setattr(trader, "_post", fake_post)
    result = trader.submit_order({
        "symbol": "SPY", "side": OrderSide.SELL, "quantity": 1,
        "order_type": OrderType.STOP, "stop_price": 99.5,
    })
    assert result["id"] == "2"


def test_submit_stop_limit_order_uses_stp_lmt_price_and_aux(trader, monkeypatch):
    """STP_LMT carries the limit price in `price` and the stop in `auxPrice`."""
    monkeypatch.setattr(trader, "_tickle", lambda: None)
    monkeypatch.setattr(trader, "_resolve_conid", lambda symbol: 42)

    def fake_post(path, payload):
        order = payload["orders"][0]
        assert order["orderType"] == "STP_LMT"
        assert order["price"] == 100.0
        assert order["auxPrice"] == 99.5
        return [{"order_id": "3", "order_status": "submitted"}]

    monkeypatch.setattr(trader, "_post", fake_post)
    result = trader.submit_order({
        "symbol": "SPY", "side": OrderSide.SELL, "quantity": 1,
        "order_type": OrderType.STOP_LIMIT, "limit_price": 100.0, "stop_price": 99.5,
    })
    assert result["id"] == "3"


# --------------------------------------------------------------------------- #
# open order decoding — must invert the STP/STP_LMT price-field mapping above
# --------------------------------------------------------------------------- #

def test_open_orders_decodes_stp_stop_price_from_price_field(trader, monkeypatch):
    monkeypatch.setattr(trader, "_get", lambda *a, **k: {"orders": [
        {"orderId": "1", "ticker": "SPY", "side": "SELL", "totalSize": 1,
         "orderType": "STP", "price": "99.5", "status": "PreSubmitted"},
    ]})
    orders = trader.open_orders()
    assert orders[0]["type"] == OrderType.STOP
    assert orders[0]["limit_price"] is None
    assert orders[0]["stop_price"] == 99.5


def test_open_orders_decodes_stp_lmt_limit_and_stop(trader, monkeypatch):
    monkeypatch.setattr(trader, "_get", lambda *a, **k: {"orders": [
        {"orderId": "2", "ticker": "SPY", "side": "SELL", "totalSize": 1,
         "orderType": "STP_LMT", "price": "100", "auxPrice": "99.5", "status": "PreSubmitted"},
    ]})
    orders = trader.open_orders()
    assert orders[0]["type"] == OrderType.STOP_LIMIT
    assert orders[0]["limit_price"] == 100.0
    assert orders[0]["stop_price"] == 99.5


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


def test_positions_short_position_pl_pct_uses_positive_denominator(trader, monkeypatch):
    """qty * avg_cost is negative for a short — dividing by the raw (negative)
    cost basis would flip the sign of a profitable short's pl_pct."""
    monkeypatch.setattr(trader, "_tickle", lambda: None)
    monkeypatch.setattr(trader, "_get", lambda *a, **k: [
        {"ticker": "TSLA", "position": -10, "avgCost": 100, "mktValue": -900, "unrealizedPnl": 100},
    ])
    positions = trader.positions()
    assert positions[0]["side"] == "short"
    assert positions[0]["unrealized_pl_pct"] == pytest.approx(0.1)


def test_positions_fetches_every_page(trader, monkeypatch):
    """CPAPI caps each page at 100 entries — a full first page means keep going."""
    page0 = [
        {"ticker": f"SYM{i}", "position": 1, "avgCost": 10, "mktValue": 10, "unrealizedPnl": 0}
        for i in range(100)
    ]
    page1 = [
        {"ticker": "LAST", "position": 1, "avgCost": 10, "mktValue": 10, "unrealizedPnl": 0},
    ]
    calls = []

    def fake_get(path, params=None):
        calls.append(path)
        if path == "/portfolio/accounts":
            return []
        if path.endswith("/positions/0"):
            return page0
        if path.endswith("/positions/1"):
            return page1
        raise AssertionError(f"unexpected page request: {path}")

    monkeypatch.setattr(trader, "_get", fake_get)
    positions = trader.positions()
    assert len(positions) == 101
    assert positions[-1]["symbol"] == "LAST"
    assert calls == [
        "/portfolio/accounts",
        "/portfolio/U123/positions/0",
        "/portfolio/U123/positions/1",
    ]


def test_account_and_positions_init_portfolio_context_once(trader, monkeypatch):
    """CPAPI requires /portfolio/accounts before any /portfolio/{id}/* call —
    it should fire once, before the first account-specific request, and be
    cached across subsequent calls within the same trader instance."""
    calls = []

    def fake_get(path, params=None):
        calls.append(path)
        if path == "/portfolio/accounts":
            return [{"accountId": "U123"}]
        if path.endswith("/summary"):
            return {"netliquidation": {"amount": 1}}
        if path.endswith("/positions/0"):
            return []
        raise AssertionError(f"unexpected path: {path}")

    monkeypatch.setattr(trader, "_get", fake_get)
    trader.account()
    trader.positions()

    assert calls[0] == "/portfolio/accounts"
    assert calls.count("/portfolio/accounts") == 1


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
