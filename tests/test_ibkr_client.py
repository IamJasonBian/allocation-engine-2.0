"""Tests for the ib_async IBKR client — focus on always-PEG-STK + conditional cancel."""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.brokers.ibkr import orders as O
from app.brokers.ibkr import positions as P
from app.brokers.ibkr.client import IBKRTrader


# -- fakes ----------------------------------------------------------------------

class FakeSession:
    """Runs broker coroutines synchronously against a mock IB object."""

    def __init__(self, ib):
        self.ib = ib

    def ensure_auth(self):
        pass

    def is_connected(self):
        return True

    def run(self, coro, timeout=30):
        loop = asyncio.new_event_loop()
        try:
            return loop.run_until_complete(coro)
        finally:
            loop.close()


def _trade(order_id=1, status="Submitted"):
    return SimpleNamespace(
        order=SimpleNamespace(orderId=order_id),
        orderStatus=SimpleNamespace(status=status),
    )


def _make_ib(*, delta=None, midpoint=2.0, place_return=None, place_side_effect=None):
    ib = MagicMock()
    ib.qualifyContractsAsync = AsyncMock(return_value=[SimpleNamespace(conId=111)])
    mg = SimpleNamespace(delta=delta) if delta is not None else None
    ticker = SimpleNamespace(modelGreeks=mg, midpoint=lambda: midpoint)
    ib.reqTickersAsync = AsyncMock(return_value=[ticker])
    if place_side_effect is not None:
        ib.placeOrder = MagicMock(side_effect=place_side_effect)
    else:
        ib.placeOrder = MagicMock(return_value=place_return or _trade())
    return ib


def _trader(ib):
    return IBKRTrader("DU123", session=FakeSession(ib))


def _opt_order(**over):
    o = {
        "chain_symbol": "SPY", "option_type": "call", "strike": 500.0,
        "expiration": "2026-06-19", "side": "BUY", "quantity": 1,
    }
    o.update(over)
    return o


# -- pure builders --------------------------------------------------------------

def test_signed_delta_sign_by_right():
    assert O.signed_delta("call", 0.4) == 0.4
    assert O.signed_delta("put", 0.4) == -0.4
    assert O.signed_delta("put", -0.4) == -0.4  # magnitude is normalized first


def test_build_peg_stk_order_shape():
    o = O.build_peg_stk_order("BUY", 3, 0.5, starting_price=1.25)
    assert o.orderType == "PEG STK"
    assert o.action == "BUY"
    assert o.totalQuantity == 3
    assert o.delta == 0.5
    assert o.startingPrice == 1.25
    assert o.tif == "DAY"


def test_build_price_conditions_band():
    conds, cancel = O.build_price_conditions(111, above=520.0, below=480.0)
    assert cancel is True
    assert len(conds) == 2
    assert conds[0].isMore is True and conds[0].price == 520.0
    assert conds[1].isMore is False and conds[1].price == 480.0
    assert all(c.conjunction == "o" for c in conds)  # OR-joined band


def test_build_price_conditions_single():
    conds, cancel = O.build_price_conditions(111, below=480.0)
    assert cancel is True and len(conds) == 1 and conds[0].isMore is False


def test_order_is_rejected():
    assert O.order_is_rejected(_trade(status="Rejected")) is True
    assert O.order_is_rejected(_trade(status="Inactive")) is True
    assert O.order_is_rejected(_trade(status="Submitted")) is False


# -- submit_option_order (the core feature) ------------------------------------

def test_submit_option_order_always_peg_stk_signed_delta():
    ib = _make_ib()
    res = _trader(ib).submit_option_order(_opt_order(option_type="put", peg_delta=0.33))
    assert res == {"id": "1", "symbol": "SPY", "status": "Submitted"}
    (_, placed_order) = ib.placeOrder.call_args[0]
    assert placed_order.orderType == "PEG STK"
    assert placed_order.delta == -0.33  # put -> negative


def test_submit_option_order_uses_live_greek_then_default():
    # greek available -> used
    ib = _make_ib(delta=-0.6)
    _trader(ib).submit_option_order(_opt_order(option_type="put"))
    assert ib.placeOrder.call_args[0][1].delta == -0.6  # abs(0.6) signed for put

    # greek unavailable -> peg_delta_default (0.5), signed for call
    ib2 = _make_ib(delta=None)
    _trader(ib2).submit_option_order(_opt_order(option_type="call"))
    assert ib2.placeOrder.call_args[0][1].delta == 0.5


def test_submit_option_order_attaches_cancel_conditions():
    ib = _make_ib()
    _trader(ib).submit_option_order(
        _opt_order(cancel_if_underlying_above=520.0, cancel_if_underlying_below=480.0)
    )
    placed = ib.placeOrder.call_args[0][1]
    assert placed.conditionsCancelOrder is True
    assert len(placed.conditions) == 2


def test_submit_option_order_peg_rejection_falls_back_to_limit():
    ib = _make_ib(place_side_effect=[_trade(status="Rejected"), _trade(order_id=2, status="Submitted")])
    res = _trader(ib).submit_option_order(_opt_order(limit_price=1.5))
    assert ib.placeOrder.call_count == 2
    assert ib.placeOrder.call_args_list[1][0][1].orderType == "LMT"
    assert res["id"] == "2"


def test_submit_option_order_returns_none_on_qualify_failure():
    ib = _make_ib()
    ib.qualifyContractsAsync = AsyncMock(return_value=[])  # cannot qualify
    assert _trader(ib).submit_option_order(_opt_order()) is None


def test_submit_option_order_returns_none_on_exception():
    ib = _make_ib()
    ib.placeOrder = MagicMock(side_effect=RuntimeError("boom"))
    assert _trader(ib).submit_option_order(_opt_order(peg_delta=0.3)) is None


# -- equity + auth --------------------------------------------------------------

def test_submit_equity_order():
    ib = _make_ib()
    res = _trader(ib).submit_order(
        {"symbol": "AAPL", "side": "BUY", "quantity": 10, "order_type": "MKT"}
    )
    assert res["symbol"] == "AAPL"
    assert ib.placeOrder.call_args[0][1].orderType == "MKT"


def test_auth_status():
    st = _trader(_make_ib()).auth_status()
    assert st == {"connected": True, "paper": True, "account_id": "DU123"}


# -- mappers --------------------------------------------------------------------

def test_map_account_summary():
    rows = [
        SimpleNamespace(tag="NetLiquidation", value="1000"),
        SimpleNamespace(tag="TotalCashValue", value="400"),
        SimpleNamespace(tag="BuyingPower", value="2000"),
        SimpleNamespace(tag="GrossPositionValue", value="600"),
    ]
    assert P.map_account_summary(rows) == {
        "equity": 1000.0, "cash": 400.0, "buying_power": 2000.0, "portfolio_value": 600.0,
    }


def test_map_option_position():
    contract = SimpleNamespace(
        secType="OPT", symbol="SPY", right="C", strike=500.0,
        lastTradeDateOrContractMonth="20260619", multiplier="100",
    )
    item = SimpleNamespace(contract=contract, position=2, averageCost=250.0,
                           marketValue=600.0, unrealizedPNL=100.0)
    m = P.map_option_position(item, ticker=None)
    assert m["chain_symbol"] == "SPY"
    assert m["option_type"] == "call"
    assert m["strike"] == 500.0
    assert m["expiration"] == "2026-06-19"
    assert m["avg_price"] == 2.5   # 250 / 100 multiplier
    assert m["quantity"] == 2
