"""spread_manager: lifecycle decisions and sandbox limits, against a fake gateway."""

from datetime import date, datetime
from zoneinfo import ZoneInfo

import pytest

from app.spread_manager import (Config, SandboxViolation, SpreadSpec, step,
                                take_profit_credit, tick_round, validate)

ET = ZoneInfo("America/New_York")
SPEC = SpreadSpec("ord-1", "L-id", "S-id", "AMD", date(2026, 11, 4), 470.0, 510.0)


def order(state="filled", qty=1, premium=1280.0, legs=None):
    legs = legs or [
        {"option": "https://x/options/instruments/L-id/", "side": "buy", "position_effect": "open"},
        {"option": "https://x/options/instruments/S-id/", "side": "sell", "position_effect": "open"}]
    return {"id": "ord-1", "state": state, "direction": "debit", "legs": legs,
            "processed_quantity": str(qty), "processed_premium": str(premium)}


class FakeGW:
    def __init__(self, order_, closes=None, open_qty=1, bid=20.0):
        self._order, self.closes, self._qty, self.bid = order_, closes or [], open_qty, bid
        self.extra = {}
        self.placed, self.cancelled = [], []

    def order(self, oid):
        return self.extra.get(oid, self._order)
    def open_close_orders(self, _): return list(self.closes)
    def open_qty(self, _): return self._qty
    def spread_quote(self, _): return self.bid, self.bid + 0.4
    def place_close(self, spec, qty, credit):
        self.placed.append((qty, credit)); return "close-1"
    def cancel_close(self, spec, oid): self.cancelled.append(oid)


LIVE = Config(dry_run=False)


def at(day, hh, mm=0): return datetime(2026, 11, day, hh, mm, tzinfo=ET)


def test_take_profit_price_is_half_of_max_profit():
    assert take_profit_credit(12.80, 40, 50) == 26.40
    assert tick_round(26.43) == 26.45 and tick_round(2.456) == 2.46


def test_waits_while_order_open():
    assert step(FakeGW(order("queued")), SPEC, LIVE, at(3, 10)) == "wait:fill"


def test_not_filled_ends():
    assert step(FakeGW(order("cancelled")), SPEC, LIVE, at(3, 10)) == "done:not_filled"


def test_places_take_profit_after_fill():
    gw = FakeGW(order())
    assert step(gw, SPEC, LIVE, at(3, 10)).startswith("placed:tp@26.40")
    assert gw.placed == [(1, 26.40)]


def test_dedupes_existing_close():
    gw = FakeGW(order(), closes=[{"id": "c1", "price": "26.40"}])
    assert step(gw, SPEC, LIVE, at(3, 10)).startswith("hold:tp")
    assert gw.placed == []


def test_guard_reprices_to_bid_on_expiry_day():
    gw = FakeGW(order(), closes=[{"id": "c1", "price": "26.40"}], bid=31.0)
    out = step(gw, SPEC, LIVE, at(4, 10, 0))
    assert gw.cancelled == ["c1"] and gw.placed == [(1, 31.0)] and out.startswith("placed:guard")


def test_hard_time_sells_for_any_bid():
    gw = FakeGW(order(), closes=[{"id": "c1", "price": "9.00"}])
    step(gw, SPEC, LIVE, at(4, 12, 35))
    assert gw.placed == [(1, 0.05)]


def test_done_when_position_closed_after_having_been_open():
    memo = {"saw_open_position": True}
    assert step(FakeGW(order(), open_qty=0), SPEC, LIVE, at(3, 10), memo) == "done:closed"


def test_zero_qty_before_position_visible_waits():
    assert step(FakeGW(order(), open_qty=0), SPEC, LIVE, at(3, 10)) == "wait:position"


def test_failed_position_read_waits():
    assert step(FakeGW(order(), open_qty=None), SPEC, LIVE, at(3, 10)) == "wait:position_read"


def test_expired_after_expiration_date():
    assert step(FakeGW(order()), SPEC, LIVE, at(5, 10)) == "done:expired"


def test_dry_run_never_places_or_cancels():
    gw = FakeGW(order(), closes=[{"id": "c1", "price": "26.40"}], bid=31.0)
    step(gw, SPEC, Config(dry_run=True), at(4, 13))
    gw2 = FakeGW(order())
    assert step(gw2, SPEC, Config(dry_run=True), at(3, 10)).startswith("dry:place")
    assert gw.cancelled == [] and gw.placed == [] and gw2.placed == []


def test_sandbox_rejects_other_legs():
    bad = order(legs=[
        {"option": "https://x/options/instruments/L-id/", "side": "buy", "position_effect": "open"},
        {"option": "https://x/options/instruments/OTHER/", "side": "sell", "position_effect": "open"}])
    with pytest.raises(SandboxViolation):
        validate(SPEC, bad)
    with pytest.raises(SandboxViolation):
        step(FakeGW(bad), SPEC, LIVE, at(3, 10))


def test_sandbox_rejects_other_order_id():
    other = order(); other["id"] = "someone-else"
    with pytest.raises(SandboxViolation):
        validate(SPEC, other)


def test_no_duplicate_when_open_order_listing_is_blind():
    """Even if open_close_orders() never returns our order, the remembered id stops repeats."""
    gw = FakeGW(order())
    memo = {}
    step(gw, SPEC, LIVE, at(3, 10), memo)
    assert memo["close_id"] == "close-1" and len(gw.placed) == 1
    gw.extra["close-1"] = {"id": "close-1", "state": "confirmed", "price": "26.40"}
    for _ in range(3):
        assert step(gw, SPEC, LIVE, at(3, 10, 1), memo).startswith("hold:tp")
    assert len(gw.placed) == 1


def test_remembered_close_filled_ends():
    gw = FakeGW(order())
    memo = {"close_id": "close-1"}
    gw.extra["close-1"] = {"id": "close-1", "state": "filled"}
    assert step(gw, SPEC, LIVE, at(3, 10), memo) == "done:closed"


def test_holds_through_report_then_places_tp():
    cfg = Config(dry_run=False, tp_not_before=datetime(2026, 11, 4, 9, 30, tzinfo=ET))
    gw = FakeGW(order())
    assert step(gw, SPEC, cfg, at(3, 9, 40)) == "hold:pre_report"
    assert step(gw, SPEC, cfg, at(3, 16, 30)) == "hold:pre_report"
    assert gw.placed == []
    assert step(gw, SPEC, cfg, at(4, 9, 31)).startswith("placed:tp@26.40")


def test_config_from_env_parses_tp_not_before():
    from app.spread_manager import config_from_env
    c = config_from_env({"SPREAD_TP_NOT_BEFORE_ET": "2026-11-04T09:30"})
    assert c.tp_not_before == datetime(2026, 11, 4, 9, 30, tzinfo=ET)
