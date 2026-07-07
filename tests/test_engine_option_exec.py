"""Tests for engine option-order execution + market-session gating."""

import datetime as dt
from unittest.mock import MagicMock

import pytest

from app.engine import AllocationEngine
from app.market_calendar import is_tradeable


# 2026-06-02 is a Tuesday (normal trading day). 2026-06-06 is a Saturday.
TRADEABLE = dt.datetime(2026, 6, 2, 11, 0)     # mid-session
PRE_OPEN = dt.datetime(2026, 6, 2, 9, 0)
POST_CLOSE = dt.datetime(2026, 6, 2, 16, 30)
OPEN_BUFFER = dt.datetime(2026, 6, 2, 9, 31)    # open 9:30 + 2m buffer -> blocked until 9:32
CLOSE_BUFFER = dt.datetime(2026, 6, 2, 15, 57)  # close 16:00 - 5m buffer -> blocked from 15:55
WEEKEND = dt.datetime(2026, 6, 6, 11, 0)


def _engine(dry_run):
    return AllocationEngine(trader=MagicMock(), runtime=MagicMock(), dry_run=dry_run)


def _order(**over):
    o = {
        "chain_symbol": "SPY", "option_type": "put", "strike": 400.0,
        "expiration": "2026-06-19", "side": "BUY", "quantity": 1, "limit_price": 1.25,
    }
    o.update(over)
    return o


# -- market_calendar.is_tradeable -----------------------------------------------

@pytest.mark.parametrize("now,expected", [
    (TRADEABLE, True),
    (PRE_OPEN, False),
    (POST_CLOSE, False),
    (OPEN_BUFFER, False),
    (CLOSE_BUFFER, False),
    (WEEKEND, False),
])
def test_is_tradeable(now, expected):
    allowed, reason = is_tradeable(now, open_buffer_min=2, close_buffer_min=5)
    assert allowed is expected
    assert reason  # always explains


# -- dry-run vs live ------------------------------------------------------------

def test_dry_run_does_not_submit():
    eng = _engine(dry_run=True)
    eng._execute_option_orders([_order()], [], now_et=TRADEABLE,
                               open_buffer_min=2, close_buffer_min=5)
    eng.trader.submit_option_order.assert_not_called()


def test_live_submits_each_order_in_rth():
    eng = _engine(dry_run=False)
    eng.trader.submit_option_order.return_value = {"id": "1", "symbol": "SPY", "status": "Submitted"}
    eng._execute_option_orders([_order(), _order(chain_symbol="QQQ")], [],
                               now_et=TRADEABLE, open_buffer_min=2, close_buffer_min=5)
    assert eng.trader.submit_option_order.call_count == 2


def test_live_caps_quantity_and_preserves_peg_cancel_fields():
    eng = _engine(dry_run=False)
    eng.max_option_order_qty = 10
    eng.trader.submit_option_order.return_value = {"id": "1", "symbol": "SPY", "status": "ok"}
    eng._execute_option_orders(
        [_order(quantity=999, peg_delta=0.3, cancel_if_underlying_below=390.0,
                cancel_if_underlying_above=420.0)],
        [], now_et=TRADEABLE, open_buffer_min=2, close_buffer_min=5,
    )
    (passed,), _ = eng.trader.submit_option_order.call_args
    assert passed["quantity"] == 10                       # capped
    assert passed["peg_delta"] == 0.3                     # preserved
    assert passed["cancel_if_underlying_below"] == 390.0  # preserved
    assert passed["cancel_if_underlying_above"] == 420.0


@pytest.mark.parametrize("now", [PRE_OPEN, POST_CLOSE, OPEN_BUFFER, CLOSE_BUFFER, WEEKEND])
def test_live_skips_outside_window(now):
    eng = _engine(dry_run=False)
    eng._execute_option_orders([_order()], [], now_et=now,
                               open_buffer_min=2, close_buffer_min=5)
    eng.trader.submit_option_order.assert_not_called()


def test_broker_without_method_is_skipped(caplog):
    eng = _engine(dry_run=False)
    del eng.trader.submit_option_order  # broker doesn't support option orders
    # Should not raise; just warn and skip.
    eng._execute_option_orders([_order()], [], now_et=TRADEABLE,
                               open_buffer_min=2, close_buffer_min=5)


def test_stale_cancel_is_logged_only():
    eng = _engine(dry_run=False)
    eng._execute_option_orders([], ["stale-1", "stale-2"], now_et=TRADEABLE,
                               open_buffer_min=2, close_buffer_min=5)
    eng.trader.cancel_order.assert_not_called()
