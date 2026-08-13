"""Tests for the engine's per-order share/dollar limits and submit resilience.

No network: fake broker and runtime clients stand in. The point of these tests
is that no order leaves the engine above either limit, that a clamped order
does not re-fire on every snapshot, and that one broker failure cannot strand
the rest of the batch.
"""

import pytest

from app.engine import AllocationEngine, apply_order_limits, order_qty
from app.enums import RiskEventType
from app.risk.observer import RiskObserver, RiskSubject


# --------------------------------------------------------------------------- #
# fakes
# --------------------------------------------------------------------------- #

class FakeRuntime:
    def __init__(self, orders, snapshot_key="snap-1", prices=None):
        self._orders = orders
        self.snapshot_key = snapshot_key
        self._prices = prices or {}

    def state(self):
        return {"snapshot_key": self.snapshot_key}

    def orders(self):
        return {"stock_orders": list(self._orders), "option_orders": []}

    def market_data(self):
        return {"tickers": {s: {"price": p, "drift_pct": 0.0}
                            for s, p in self._prices.items()}}


class FakeBroker:
    """Records submissions; `fail_on` symbols raise instead."""

    def __init__(self, open_orders=None, fail_on=()):
        self._open = open_orders or []
        self.fail_on = set(fail_on)
        self.submitted = []

    def open_orders(self):
        return list(self._open)

    def positions(self):
        return []

    def submit_order(self, order):
        if order["symbol"] in self.fail_on:
            raise RuntimeError(f"broker rejected {order['symbol']}")
        self.submitted.append(order)
        return {"id": f"ord-{len(self.submitted)}", "state": "queued"}


class RecordingObserver(RiskObserver):
    def __init__(self):
        self.events = []

    def update(self, symbol, price):
        pass

    def on_risk_event(self, event):
        self.events.append(event)


def _engine(runtime, broker, **kwargs):
    subject = RiskSubject()
    observer = RecordingObserver()
    subject.attach(observer)
    engine = AllocationEngine(trader=broker, runtime=runtime, dry_run=False,
                              risk_subject=subject, **kwargs)
    return engine, observer


def _order(symbol="AAPL", side="BUY", quantity=100, limit_price=10.0):
    return {"symbol": symbol, "side": side, "quantity": quantity,
            "limit_price": limit_price}


# --------------------------------------------------------------------------- #
# order_qty — the quantity/qty key split
# --------------------------------------------------------------------------- #

@pytest.mark.parametrize("order,expected", [
    ({"quantity": 10}, 10.0),
    ({"qty": 10}, 10.0),
    ({"qty": "10"}, 10.0),          # broker book stringifies
    ({"quantity": 0, "qty": 5}, 0.0),  # explicit 0 wins over the alias
    ({}, None),
    ({"quantity": None, "qty": None}, None),
    ({"quantity": "abc"}, None),
])
def test_order_qty_reads_either_key(order, expected):
    assert order_qty(order) == expected


def test_execute_does_not_keyerror_on_qty_only_order():
    """A broker-shaped order (`qty`) must not blow up the batch."""
    broker = FakeBroker()
    runtime = FakeRuntime([{"symbol": "AAPL", "side": "BUY", "qty": 5,
                            "limit_price": 10.0}])
    engine, _ = _engine(runtime, broker, max_order_qty=50)
    engine.tick()
    assert len(broker.submitted) == 1
    assert broker.submitted[0]["quantity"] == 5


# --------------------------------------------------------------------------- #
# apply_order_limits — the pure clamp
# --------------------------------------------------------------------------- #

def test_qty_limit_trims_and_reports():
    limited, adj = apply_order_limits(_order(quantity=100), max_qty=50)
    assert limited["quantity"] == 50
    assert adj == {"symbol": "AAPL", "side": "BUY", "action": "capped",
                   "reason": "qty_limit", "requested_qty": 100.0,
                   "effective_qty": 50.0, "limit_price": 10.0}


def test_notional_limit_binds_before_qty_limit():
    """50 shares of a $1,000 stock is a $50k order — the bug from issue #55."""
    limited, adj = apply_order_limits(
        _order(quantity=100, limit_price=1000.0), max_qty=50, max_notional=10_000)
    assert limited["quantity"] == 10          # 10 x $1,000 = $10,000
    assert adj["reason"] == "notional_limit"


def test_notional_limit_rounds_down_never_up():
    limited, _ = apply_order_limits(
        _order(quantity=100, limit_price=300.0), max_notional=1000)
    assert limited["quantity"] == 3           # 3.33 -> 3, so $999 <= $1,000
    assert limited["quantity"] * 300.0 <= 1000


def test_order_within_both_limits_passes_untouched():
    order = _order(quantity=10, limit_price=10.0)
    limited, adj = apply_order_limits(order, max_qty=50, max_notional=10_000)
    assert adj is None
    assert limited == order


def test_market_order_prices_off_the_live_quote():
    order = {"symbol": "AAPL", "side": "BUY", "quantity": 100}
    limited, adj = apply_order_limits(order, max_notional=1000, price=100.0)
    assert limited["quantity"] == 10
    assert adj["reason"] == "notional_limit"


def test_unpriceable_order_is_skipped_when_a_dollar_cap_is_set():
    """Fail closed: no limit price and no quote means no dollar bound."""
    order = {"symbol": "AAPL", "side": "BUY", "quantity": 100}
    limited, adj = apply_order_limits(order, max_qty=50, max_notional=1000)
    assert limited is None
    assert adj["reason"] == "unpriceable_under_notional_limit"


def test_unpriceable_order_passes_when_no_dollar_cap_is_set():
    """Byte-for-byte prior behaviour when MAX_ORDER_NOTIONAL is unset."""
    order = {"symbol": "AAPL", "side": "BUY", "quantity": 100}
    limited, adj = apply_order_limits(order, max_qty=50)
    assert limited["quantity"] == 50
    assert adj["action"] == "capped"


def test_notional_below_one_share_is_skipped_not_zeroed():
    limited, adj = apply_order_limits(
        _order(quantity=10, limit_price=5000.0), max_notional=1000)
    assert limited is None
    assert adj["reason"] == "notional_limit_below_one_unit"


def test_fractional_order_stays_fractional():
    limited, _ = apply_order_limits(
        _order(quantity=2.5, limit_price=100.0), max_notional=100)
    assert limited["quantity"] == pytest.approx(1.0)


def test_missing_quantity_is_skipped():
    limited, adj = apply_order_limits({"symbol": "AAPL", "side": "BUY"}, max_qty=50)
    assert limited is None
    assert adj["reason"] == "no_quantity"


def test_limited_order_carries_one_size_key():
    limited, _ = apply_order_limits(
        {"symbol": "AAPL", "side": "BUY", "qty": 100, "limit_price": 10.0},
        max_qty=50)
    assert limited["quantity"] == 50
    assert "qty" not in limited


def test_limits_do_not_mutate_the_caller_s_order():
    order = _order(quantity=100)
    apply_order_limits(order, max_qty=50)
    assert order["quantity"] == 100


# --------------------------------------------------------------------------- #
# engine integration — reconciliation, events, batch resilience
# --------------------------------------------------------------------------- #

def test_capped_order_is_not_resubmitted_on_the_next_snapshot():
    """The capped order must reconcile against the open order it produced.

    Capping inside _execute() left the desired key (100) unmatched by the open
    order it created (50), so every fresh snapshot submitted another 50.
    """
    broker = FakeBroker()
    runtime = FakeRuntime([_order(quantity=100)])
    engine, _ = _engine(runtime, broker, max_order_qty=50)

    engine.tick()
    assert len(broker.submitted) == 1
    assert broker.submitted[0]["quantity"] == 50

    # The capped order is now live in the broker book; a new snapshot arrives.
    broker._open = [{"symbol": "AAPL", "side": "BUY", "qty": 50,
                     "limit_price": 10.0, "id": "ord-1"}]
    runtime.snapshot_key = "snap-2"
    engine.tick()
    assert len(broker.submitted) == 1, "capped order re-fired on the next snapshot"


def test_capping_emits_a_risk_event_and_lands_in_the_report():
    broker = FakeBroker()
    runtime = FakeRuntime([_order(quantity=100)])
    engine, observer = _engine(runtime, broker, max_order_qty=50)
    engine.tick()

    capped = [e for e in observer.events
              if e.event_type == RiskEventType.ORDER_CAPPED]
    assert len(capped) == 1
    assert capped[0].symbol == "AAPL"
    assert capped[0].snapshot_key == "snap-1"
    assert capped[0].metadata["requested_qty"] == 100.0
    assert capped[0].metadata["effective_qty"] == 50.0

    report = engine.last_execution_report
    assert report["submitted"] == 1
    assert len(report["capped"]) == 1
    assert report["skipped"] == []


def test_skipped_order_emits_an_event_and_is_never_submitted():
    broker = FakeBroker()
    # Market order (no limit price), no quote for the symbol, dollar cap on.
    runtime = FakeRuntime([{"symbol": "AAPL", "side": "BUY", "quantity": 100}])
    engine, observer = _engine(runtime, broker, max_order_qty=50,
                               max_order_notional=10_000)
    engine.tick()

    assert broker.submitted == []
    assert [e.event_type for e in observer.events] == [RiskEventType.ORDER_SKIPPED]
    assert engine.last_execution_report["skipped"][0]["symbol"] == "AAPL"


def test_engine_prices_market_orders_from_the_tick_s_market_data():
    broker = FakeBroker()
    runtime = FakeRuntime([{"symbol": "AAPL", "side": "BUY", "quantity": 100}],
                          prices={"AAPL": 100.0})
    engine, _ = _engine(runtime, broker, max_order_qty=50,
                        max_order_notional=1000)
    engine.tick()
    assert [o["quantity"] for o in broker.submitted] == [10]


def test_one_broker_failure_does_not_strand_the_rest_of_the_batch():
    broker = FakeBroker(fail_on={"AAPL"})
    runtime = FakeRuntime([_order(symbol="AAPL", quantity=10),
                           _order(symbol="MSFT", quantity=10),
                           _order(symbol="NVDA", quantity=10)])
    engine, observer = _engine(runtime, broker, max_order_qty=50)
    engine.tick()

    assert [o["symbol"] for o in broker.submitted] == ["MSFT", "NVDA"]
    rejected = [e for e in observer.events
                if e.event_type == RiskEventType.ORDER_REJECTED]
    assert len(rejected) == 1 and rejected[0].symbol == "AAPL"
    assert engine.last_execution_report["failed"][0]["symbol"] == "AAPL"


def test_standing_cap_alerts_once_then_again_when_it_changes_shape():
    """A steady over-limit request must not re-alert on every snapshot."""
    broker = FakeBroker()
    runtime = FakeRuntime([_order(quantity=100)])
    engine, observer = _engine(runtime, broker, max_order_qty=50)

    engine.tick()
    runtime.snapshot_key = "snap-2"
    engine.tick()
    assert len(observer.events) == 1, "standing cap re-alerted unchanged"

    # The runtime service now asks for a different size — a new divergence.
    runtime._orders = [_order(quantity=200)]
    runtime.snapshot_key = "snap-3"
    engine.tick()
    assert len(observer.events) == 2
    assert observer.events[1].metadata["requested_qty"] == 200.0

    # It falls back within the limit, then breaches again — alert again.
    runtime._orders = [_order(quantity=10)]
    runtime.snapshot_key = "snap-4"
    engine.tick()
    runtime._orders = [_order(quantity=200)]
    runtime.snapshot_key = "snap-5"
    engine.tick()
    assert len(observer.events) == 3


def test_dollar_cap_defaults_off():
    engine, _ = _engine(FakeRuntime([]), FakeBroker())
    assert engine.max_order_notional is None
