"""Tests for the IBKR broker client — mapping logic against a fake IB client.

No gateway, no network: a stub IB module is injected into sys.modules so the
client's dict-mapping (the part this repo owns) is what's under test. The stub
is injected as both ``ib_async`` (the client's first-choice import) and
``ib_insync``, so the suite passes whether or not the real ib_async is installed
alongside the connectivity probe.
"""

import sys
import types
from types import SimpleNamespace

import pytest

from app.enums import OrderSide, OrderType


def _fake_ib_client(ib, module_name="ib_async"):
    """Build a module-shaped stub whose IB() returns the given fake."""
    mod = types.ModuleType(module_name)
    mod.IB = lambda: ib
    mod.Stock = lambda symbol, exchange, currency: SimpleNamespace(
        symbol=symbol, exchange=exchange, currency=currency
    )
    mod.Option = lambda symbol, expiry, strike, right, exchange, currency: SimpleNamespace(
        symbol=symbol, lastTradeDateOrContractMonth=expiry, strike=strike,
        right=right, exchange=exchange, currency=currency, secType="OPT",
    )
    for name, fields in {
        "MarketOrder": ("action", "totalQuantity"),
        "LimitOrder": ("action", "totalQuantity", "lmtPrice"),
        "StopOrder": ("action", "totalQuantity", "auxPrice"),
        "StopLimitOrder": ("action", "totalQuantity", "lmtPrice", "auxPrice"),
    }.items():
        def make(name=name, fields=fields):
            def ctor(*args):
                ns = SimpleNamespace(orderType=name, tif=None)
                for f, v in zip(fields, args):
                    setattr(ns, f, v)
                return ns
            return ctor
        setattr(mod, name, make())
    return mod


class FakeIB:
    def __init__(self):
        self.connected = False
        self.placed = []          # (contract, order) pairs
        self.cancelled = []
        self.global_cancels = 0
        self._summary = []
        self._portfolio = []
        self._open_trades = []
        self._trades = []
        self._executions = []

    def isConnected(self):
        return self.connected

    def connect(self, host, port, clientId):
        self.connected = True

    def accountSummary(self):
        return self._summary

    def portfolio(self):
        return self._portfolio

    def openTrades(self):
        return self._open_trades

    def trades(self):
        return self._trades

    def reqExecutions(self):
        return self._executions

    def qualifyContracts(self, contract):
        return [contract]

    def placeOrder(self, contract, order):
        self.placed.append((contract, order))
        return SimpleNamespace(
            order=SimpleNamespace(orderId=101),
            orderStatus=SimpleNamespace(status="Submitted"),
        )

    def cancelOrder(self, order):
        self.cancelled.append(order.orderId)

    def reqGlobalCancel(self):
        self.global_cancels += 1


@pytest.fixture
def fake_ib(monkeypatch):
    ib = FakeIB()
    # Inject under the client's first-choice import (ib_async) so a real
    # ib_async installed for the probe does not shadow the stub, and under
    # ib_insync for the fallback path.
    monkeypatch.setitem(sys.modules, "ib_async", _fake_ib_client(ib, "ib_async"))
    monkeypatch.setitem(sys.modules, "ib_insync", _fake_ib_client(ib, "ib_insync"))
    return ib


@pytest.fixture
def trader(fake_ib):
    from app.brokers.ibkr_client import IBKRTrader

    return IBKRTrader(host="127.0.0.1", port=4002, client_id=1, paper=True)


def test_account_maps_summary_tags(trader, fake_ib):
    fake_ib._summary = [
        SimpleNamespace(tag="NetLiquidation", value="10000.5"),
        SimpleNamespace(tag="TotalCashValue", value="2500"),
        SimpleNamespace(tag="BuyingPower", value="5000"),
    ]
    acct = trader.account()
    assert acct == {
        "equity": 10000.5,
        "cash": 2500.0,
        "buying_power": 5000.0,
        "portfolio_value": 10000.5,
    }


def test_positions_standardized_keys(trader, fake_ib):
    fake_ib._portfolio = [
        SimpleNamespace(
            contract=SimpleNamespace(symbol="IWN"),
            position=10.0, marketValue=1200.0, averageCost=100.0,
            unrealizedPNL=200.0,
        )
    ]
    (pos,) = trader.positions()
    assert pos["symbol"] == "IWN"
    assert pos["qty"] == 10.0
    assert pos["side"] == "long"
    assert pos["avg_entry"] == 100.0
    assert pos["unrealized_pl"] == 200.0
    assert pos["unrealized_pl_pct"] == pytest.approx(0.2)


def test_open_orders_maps_ib_order_types(trader, fake_ib):
    fake_ib._open_trades = [
        SimpleNamespace(
            contract=SimpleNamespace(symbol="CRWD"),
            order=SimpleNamespace(
                orderId=7, action="BUY", totalQuantity=2.0,
                orderType="STP LMT", lmtPrice=350.0, auxPrice=349.0,
            ),
            orderStatus=SimpleNamespace(status="PreSubmitted"),
        )
    ]
    (o,) = trader.open_orders()
    assert o["id"] == "7"
    assert o["side"] == "buy"
    assert o["type"] == OrderType.STOP_LIMIT
    assert o["limit_price"] == 350.0
    assert o["stop_price"] == 349.0


def test_submit_limit_order(trader, fake_ib):
    result = trader.submit_order({
        "symbol": "IWN",
        "side": OrderSide.BUY,
        "quantity": 3,
        "order_type": OrderType.LIMIT,
        "limit_price": 123.45,
    })
    assert result == {"id": "101", "symbol": "IWN", "status": "Submitted"}
    (contract, order), = fake_ib.placed
    assert contract.symbol == "IWN"
    assert order.action == "BUY"
    assert order.lmtPrice == 123.45
    assert order.tif == "GTC"


def test_submit_option_limit_order_uses_full_contract(trader, fake_ib):
    result = trader.submit_order({
        "symbol": "SPY",
        "asset_class": "option",
        "expiration": "20260918",
        "strike": "500",
        "option_type": "call",
        "side": OrderSide.BUY,
        "quantity": 2,
        "order_type": OrderType.LIMIT,
        "limit_price": 4.25,
    })

    assert result == {"id": "101", "symbol": "SPY", "status": "Submitted"}
    (contract, order), = fake_ib.placed
    assert contract.secType == "OPT"
    assert contract.lastTradeDateOrContractMonth == "20260918"
    assert contract.strike == 500.0
    assert contract.right == "C"
    assert order.lmtPrice == 4.25


def test_loads_maintained_ib_async_when_ib_insync_is_unavailable(monkeypatch):
    from app.brokers import ibkr_client

    fake = types.ModuleType("ib_async")
    real_import = __import__

    def import_module(name, *args, **kwargs):
        if name == "ib_async":
            return fake
        if name == "ib_insync":
            raise ImportError("archived client unavailable")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr("builtins.__import__", import_module)
    assert ibkr_client._load_ib_client() is fake


def test_cancel_order_matches_by_id(trader, fake_ib):
    fake_ib._open_trades = [
        SimpleNamespace(order=SimpleNamespace(orderId=7),
                        contract=SimpleNamespace(symbol="X"),
                        orderStatus=SimpleNamespace(status="Submitted")),
    ]
    trader.cancel_order("7")
    assert fake_ib.cancelled == [7]


def test_connect_error_names_the_gateway(fake_ib, monkeypatch):
    from app.brokers.ibkr_client import IBKRTrader

    def boom(host, port, clientId):
        raise OSError("connection refused")

    monkeypatch.setattr(fake_ib, "connect", boom)
    trader = IBKRTrader(host="127.0.0.1", port=4002, client_id=1)
    with pytest.raises(ConnectionError, match="IB Gateway"):
        trader.account()


def test_portfolio_detail_keeps_option_fields(trader, fake_ib):
    fake_ib._portfolio = [
        SimpleNamespace(
            contract=SimpleNamespace(symbol="AMD", secType="STK"),
            position=100.0, marketValue=9700.0, averageCost=95.0,
            unrealizedPNL=200.0,
        ),
        SimpleNamespace(
            contract=SimpleNamespace(
                symbol="AMD", secType="OPT", right="C", strike=105.0,
                lastTradeDateOrContractMonth="20260918",
            ),
            position=-1.0, marketValue=-120.0, averageCost=180.0,
            unrealizedPNL=60.0,
        ),
    ]
    stk, opt = trader.portfolio_detail()
    assert stk["sec_type"] == "STK"
    assert stk["right"] is None and stk["strike"] is None and stk["expiry"] is None
    assert opt == {
        "symbol": "AMD", "sec_type": "OPT", "right": "C", "strike": 105.0,
        "expiry": "20260918", "qty": -1.0, "avg_cost": 180.0,
        "market_value": -120.0, "unrealized_pl": 60.0,
    }
