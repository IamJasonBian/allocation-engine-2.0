"""Option take-profit sweep: placement via the broker, done marker, spread and qty safety."""

import logging

import pytest
import requests

from app.stop_sweeper import StopStore, option_take_profit_limit, sweep_options_take_profit
from app.utils_shared import SweepState, maybe_option_take_profit_sweep


class FakeBroker:
    """Records take-profit placements; ``result`` (or ``exc``) sets the reply."""

    def __init__(self, result=None, exc=None):
        self.placed = []
        self.result = {"id": "tp-1", "state": "queued"} if result is None else result
        self.exc = exc

    def place_option_take_profit(self, order):
        self.placed.append(order)
        if self.exc:
            raise self.exc
        return self.result


@pytest.fixture
def store(tmp_path):
    return StopStore(str(tmp_path / "stops.sqlite3"))


def pos(**over):
    base = {"chain_symbol": "MU", "option_type": "call", "strike": 95.0,
            "expiration": "2026-03-20", "position_type": "long", "quantity": 2,
            "purchase_price": 2.0}
    base.update(over)
    return base


def sell_close(qty, price=1.0, trigger="stop", strike=95.0, filled=0):
    return {"state": "queued", "quantity": qty, "filled_quantity": filled,
            "price": price, "trigger": trigger,
            "legs": [{"side": "sell", "position_effect": "close", "chain_symbol": "MU",
                      "option_type": "call", "strike": strike,
                      "expiration": "2026-03-20"}]}


def run(broker, store, positions, orders=(), dry_run=False, tp=50):
    return sweep_options_take_profit(broker, store, positions, list(orders),
                                     tp_percent=tp, dry_run=dry_run)


def reasons(out):
    return [s["reason"] for s in out["skipped"]]


def test_limit_price_is_on_a_valid_tick():
    assert option_take_profit_limit(2.0, 50) == 3.0
    assert option_take_profit_limit(2.13, 75) == 3.7          # 3.7275 -> $0.10 tick
    assert option_take_profit_limit(1.13, 75) == 1.95         # 1.9775 -> $0.05 tick


def test_live_places_sell_to_close_through_broker_and_marks_done(store):
    broker = FakeBroker()
    out = run(broker, store, [pos()])
    assert len(out["placed"]) == 1 and out["failed"] == []
    order = broker.placed[0]
    assert order["action"] == "sell_to_close" and order["quantity"] == 2
    assert order["limit_price"] == "3.0"
    assert store.option_tp_swept_today()


def test_dry_run_never_calls_broker(store):
    broker = FakeBroker()
    out = run(broker, store, [pos()], dry_run=True)
    assert broker.placed == [] and len(out["placed"]) == 1
    assert store.option_tp_swept_today()


def test_runs_once_per_day(store):
    broker = FakeBroker()
    run(broker, store, [pos()])
    out = run(broker, store, [pos()])
    assert out["skipped"] == "already_swept_today" and len(broker.placed) == 1


@pytest.mark.parametrize("broker", [
    FakeBroker(exc=requests.HTTPError("404 Client Error: Not Found")),
    FakeBroker(exc=ConnectionError("box unreachable")),
    FakeBroker(result={}),                                     # broker rejected
])
def test_placement_failure_does_not_mark_done(store, broker, caplog):
    caplog.set_level(logging.INFO)
    out = run(broker, store, [pos()])
    assert len(out["failed"]) == 1 and out["placed"] == []
    assert not store.option_tp_swept_today()
    assert any(r.levelname in ("WARNING", "ERROR") and "failed" in r.message
               for r in caplog.records)
    assert "failed=1" in caplog.text


def test_failure_on_one_contract_still_places_others_and_retries(store):
    class Flaky(FakeBroker):
        def place_option_take_profit(self, order):
            self.placed.append(order)
            if order["contract"]["chain_symbol"] == "NBIS":
                raise requests.HTTPError("404")
            return {"id": "ok", "state": "queued"}

    out = run(Flaky(), store, [pos(), pos(chain_symbol="NBIS")])
    assert [p["contract"]["chain_symbol"] for p in out["placed"]] == ["MU"]
    assert not store.option_tp_swept_today()


def test_business_skips_still_mark_done(store):
    positions = [pos(quantity=0.5), pos(chain_symbol="NBIS", purchase_price=0)]
    out = run(FakeBroker(), store, positions)
    assert set(reasons(out)) == {"fractional_qty", "no_purchase_price"}
    assert store.option_tp_swept_today()


def test_empty_book_does_not_mark_done(store):
    run(FakeBroker(), store, [])
    assert not store.option_tp_swept_today()


def test_skips_long_paired_with_short_on_same_chain(store):
    positions = [pos(), pos(strike=110.0, position_type="short")]
    broker = FakeBroker()
    out = run(broker, store, positions)
    assert reasons(out) == ["paired_with_short_leg"] and broker.placed == []


def test_existing_tp_at_same_price_is_skipped(store):
    out = run(FakeBroker(), store, [pos()], [sell_close(2, price=3.0, trigger="immediate")])
    assert reasons(out) == ["tp_limit_already_open"]


def test_stop_holding_full_qty_skips_with_qty_committed(store):
    broker = FakeBroker()
    out = run(broker, store, [pos()], [sell_close(2)])
    assert reasons(out) == ["qty_committed"] and broker.placed == []
    assert store.option_tp_swept_today()


def test_tp_qty_is_held_minus_committed(store):
    broker = FakeBroker()
    run(broker, store, [pos(quantity=5)], [sell_close(3, filled=1)])  # 2 still resting
    assert broker.placed[0]["quantity"] == 3


def _config(store_path, **over):
    cfg = {"OPTION_TP_ENABLED": True, "OPTION_TP_SWEEP_HOUR_ET": 0,
           "OPTION_TP_SWEEP_DRY_RUN": False, "OPTION_TP_PERCENT": 50,
           "STOP_DB_PATH": store_path}
    cfg.update(over)
    return cfg


def test_maybe_sweep_uses_loop_book_and_needs_no_box_client(tmp_path, monkeypatch):
    monkeypatch.setattr("app.utils_shared._post_bot_activity", lambda *a: None)
    path = str(tmp_path / "stops.sqlite3")
    broker, state = FakeBroker(), SweepState()
    out = maybe_option_take_profit_sweep(_config(path), broker, [pos()], [], state)
    assert len(out["placed"]) == 1 and state.client is None
    assert maybe_option_take_profit_sweep(_config(path), broker, [pos()], [], state) is None


def test_maybe_sweep_off_without_broker_support(tmp_path):
    assert maybe_option_take_profit_sweep(
        _config(str(tmp_path / "s.db")), object(), [pos()], [], SweepState()) is None
