"""Tests for AllocationEngine._execute_option_orders — option order submission."""

from unittest.mock import MagicMock

import pytest

from app.engine import AllocationEngine


def _option_order(quantity: float = 2, side: str = "buy") -> dict:
    return {
        "chain_symbol": "AAPL",
        "option_type": "call",
        "strike": 150.0,
        "expiration": "2026-07-17",
        "side": side,
        "quantity": quantity,
        "limit_price": 1.25,
        "order_type": "limit",
    }


def _engine(dry_run: bool, max_order_qty: int = 50) -> AllocationEngine:
    trader = MagicMock()
    runtime = MagicMock()
    return AllocationEngine(
        trader=trader,
        runtime=runtime,
        dry_run=dry_run,
        max_order_qty=max_order_qty,
    )


def test_dry_run_does_not_submit():
    engine = _engine(dry_run=True)
    results = engine._execute_option_orders([_option_order(), _option_order()], [])

    engine.trader.submit_option_order.assert_not_called()
    assert not results  # dry-run returns empty list


def test_live_submits_once_per_order():
    engine = _engine(dry_run=False)
    engine.trader.submit_option_order.return_value = {
        "id": "opt-1", "symbol": "AAPL", "status": "queued"
    }

    orders = [_option_order(quantity=2), _option_order(quantity=3, side="sell")]
    results = engine._execute_option_orders(orders, [])

    assert engine.trader.submit_option_order.call_count == 2
    assert len(results) == 2


def test_live_caps_quantity():
    engine = _engine(dry_run=False, max_order_qty=5)
    engine.trader.submit_option_order.return_value = {
        "id": "opt-1", "symbol": "AAPL", "status": "queued"
    }

    order = _option_order(quantity=100)
    engine._execute_option_orders([order], [])

    engine.trader.submit_option_order.assert_called_once()
    submitted = engine.trader.submit_option_order.call_args[0][0]
    assert submitted["quantity"] == 5  # capped to max_order_qty


def test_max_option_order_qty_overrides_cap():
    engine = _engine(dry_run=False, max_order_qty=50)
    engine.max_option_order_qty = 3
    engine.trader.submit_option_order.return_value = {
        "id": "opt-1", "symbol": "AAPL", "status": "queued"
    }

    order = _option_order(quantity=10)
    engine._execute_option_orders([order], [])

    submitted = engine.trader.submit_option_order.call_args[0][0]
    assert submitted["quantity"] == 3  # capped to max_option_order_qty


def test_broker_without_submit_option_order_skips():
    trader = MagicMock(spec=[])  # no submit_option_order attribute
    runtime = MagicMock()
    engine = AllocationEngine(
        trader=trader, runtime=runtime, dry_run=False, max_order_qty=50
    )

    results = engine._execute_option_orders([_option_order()], [])

    assert results == []  # nothing submitted, no crash


def test_cancel_ids_logged_only():
    engine = _engine(dry_run=False)
    engine.trader.submit_option_order.return_value = {
        "id": "opt-1", "symbol": "AAPL", "status": "queued"
    }

    # Stale cancellation is logged-only; submit_option_order is the only call path.
    engine._execute_option_orders([], ["stale-1", "stale-2"])

    engine.trader.submit_option_order.assert_not_called()
    # No cancel method should be invoked for stale ids.
    assert not engine.trader.cancel_option_order.called
