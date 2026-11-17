"""Weekend flatten: window edges, cancel-then-sell planning, sweep gating, payloads."""

from datetime import datetime
from zoneinfo import ZoneInfo

from app.risk.weekend_rules import in_weekend_window, plan_weekend_flatten
from app.utils_shared import maybe_option_stop_loss_sweep, maybe_weekend_flatten_sweep

ET = ZoneInfo("America/New_York")
FRI_1500 = datetime(2026, 11, 13, 15, 0, tzinfo=ET)
THU = datetime(2026, 11, 12, 15, 30, tzinfo=ET)


def _pos(**over):
    p = {"chain_symbol": "NBIS", "option_type": "call", "position_type": "long",
         "strike": 230.0, "expiration": "2026-11-20", "quantity": 17.0,
         "purchase_price": 1.32, "mark_price": 0.83}
    p.update(over)
    return p


def _sell(oid="stop-1", trigger="stop", state="queued", **leg):
    base = {"side": "sell", "position_effect": "close", "chain_symbol": "NBIS",
            "option_type": "call", "strike": 230.0, "expiration": "2026-11-20"}
    base.update(leg)
    return {"id": oid, "state": state, "quantity": 17, "trigger": trigger, "legs": [base]}


def test_window_edges_in_et_across_dst():
    assert not in_weekend_window(datetime(2026, 11, 13, 14, 59, tzinfo=ET), 15)
    assert in_weekend_window(FRI_1500, 15)
    assert in_weekend_window(datetime(2026, 11, 14, 9, 0, tzinfo=ET), 15)     # Sat
    assert in_weekend_window(datetime(2026, 11, 15, 23, 59, tzinfo=ET), 15)   # Sun
    assert not in_weekend_window(datetime(2026, 11, 16, 0, 0, tzinfo=ET), 15)  # Mon
    assert not in_weekend_window(THU, 15)
    # EDT Friday: 19:00Z is 15:00 ET; naive values are UTC.
    assert in_weekend_window(datetime(2026, 10, 16, 19, 0), 15)
    assert not in_weekend_window(datetime(2026, 10, 16, 18, 59), 15)
    assert in_weekend_window(datetime(2026, 11, 13, 15, 30, tzinfo=ET), 15.5)


def test_nothing_planned_outside_window():
    assert plan_weekend_flatten([_pos()], [], THU, 15, 5) == ([], [], [])


def test_sells_full_quantity_at_mark_less_offset():
    sells, cancels, skipped = plan_weekend_flatten([_pos()], [], FRI_1500, 15, 5)
    assert cancels == [] and skipped == []
    assert sells == [{"contract": {"chain_symbol": "NBIS", "option_type": "call",
                                   "strike": 230.0, "expiration": "2026-11-20"},
                      "quantity": 17, "mark": 0.83, "limit_price": 0.75}]


def test_cancels_resting_sells_first_then_sells_next_pass():
    resting = [_sell("stop-1"), _sell("tp-1", trigger="immediate")]
    sells, cancels, _ = plan_weekend_flatten([_pos()], resting, FRI_1500, 15, 5)
    assert sells == [] and [c["order_id"] for c in cancels] == ["stop-1", "tp-1"]
    done = [_sell("stop-1", state="cancelled"), _sell("other", strike=220.0)]
    sells, cancels, _ = plan_weekend_flatten([_pos()], done, FRI_1500, 15, 5)
    assert cancels == [] and sells[0]["quantity"] == 17


def test_skips_shorts_paired_longs_fractional_and_unmarked():
    vertical = [_pos(), _pos(position_type="short", strike=250.0)]
    sells, _, skipped = plan_weekend_flatten(vertical, [], FRI_1500, 15, 5)
    assert sells == [] and skipped[0]["reason"] == "paired_with_short_leg"
    _, _, skipped = plan_weekend_flatten([_pos(quantity=0.5)], [], FRI_1500, 15, 5)
    assert skipped[0]["reason"] == "fractional_qty"
    _, _, skipped = plan_weekend_flatten([_pos(mark_price=None)], [], FRI_1500, 15, 5)
    assert skipped[0]["reason"] == "no_mark"


class FakeBroker:
    def __init__(self):
        self.placed, self.cancelled = [], []

    def place_option_close_limit(self, order):
        self.placed.append(order)
        return {"id": f"oid-{len(self.placed)}", "state": "queued"}

    def cancel_option_order(self, order_id):
        self.cancelled.append(order_id)
        return True

    def place_option_stop_loss(self, order):
        self.placed.append(order)
        return {"id": "sl", "state": "queued"}


def _cfg(**over):
    c = {"OPTION_WEEKEND_FLAT_ENABLED": True, "OPTION_WEEKEND_FLAT_DRY_RUN": True,
         "OPTION_WEEKEND_CUTOFF_HOUR_ET": 15, "OPTION_WEEKEND_FLAT_LIMIT_OFFSET_PERCENT": 5,
         "OPTION_SL_ENABLED": True, "OPTION_SL_SWEEP_DRY_RUN": False,
         "OPTION_SL_SWEEP_INTERVAL_SECONDS": 900, "OPTION_SL_PERCENT": 25,
         "OPTION_SL_LIMIT_OFFSET_PERCENT": 10}
    c.update(over)
    return c


def test_sweep_dry_run_touches_nothing():
    b = FakeBroker()
    out = maybe_weekend_flatten_sweep(_cfg(), b, [_pos()], [_sell()], FRI_1500)
    assert len(out["cancels"]) == 1 and b.cancelled == [] and b.placed == []


def test_sweep_live_cancels_then_sells(monkeypatch):
    posted = []
    monkeypatch.setattr("app.utils_shared._post_bot_activity",
                        lambda events, label: posted.extend(events))
    b, cfg = FakeBroker(), _cfg(OPTION_WEEKEND_FLAT_DRY_RUN=False)
    maybe_weekend_flatten_sweep(cfg, b, [_pos()], [_sell()], FRI_1500)
    assert b.cancelled == ["stop-1"] and b.placed == []
    out = maybe_weekend_flatten_sweep(cfg, b, [_pos()], [], FRI_1500)
    assert len(out["placed"]) == 1 and b.placed[0]["limit_price"] == 0.75
    assert posted[0]["type"] == "OPTION_WEEKEND_FLATTEN"


def test_sweep_gated_off_outside_window_or_disabled():
    b = FakeBroker()
    assert maybe_weekend_flatten_sweep(_cfg(), b, [_pos()], [], THU) is None
    assert maybe_weekend_flatten_sweep(_cfg(OPTION_WEEKEND_FLAT_ENABLED=False), b,
                                       [_pos()], [], FRI_1500) is None
    assert maybe_weekend_flatten_sweep(_cfg(), object(), [_pos()], [], FRI_1500) is None


def test_stop_loss_sweep_stands_down_in_window(monkeypatch):
    monkeypatch.setattr("app.utils_shared.in_weekend_window", lambda now, cutoff: True)
    b = FakeBroker()
    assert maybe_option_stop_loss_sweep(_cfg(), b, [_pos()], [], {}) is None
    assert b.placed == []
    monkeypatch.setattr("app.utils_shared.in_weekend_window", lambda now, cutoff: False)
    monkeypatch.setattr("app.utils_shared._post_bot_activity", lambda *a: None)
    out = maybe_option_stop_loss_sweep(_cfg(), b, [_pos(mark_price=1.20)], [], {})
    assert out["planned"][0]["stop_price"] == 0.95  # 1.32 * 0.75 = 0.99 -> 0.95 tick


def test_robinhood_close_limit_and_cancel_payloads(monkeypatch):
    from app.brokers.robinhood_client import RobinhoodTrader

    seen, cancelled = {}, []
    monkeypatch.setattr("app.brokers.robinhood_client.rh.orders.order_sell_option_limit",
                        lambda **kw: seen.update(kw) or {"id": "abc", "state": "queued"})
    monkeypatch.setattr("app.brokers.robinhood_client.rh.orders.cancel_option_order",
                        cancelled.append)
    t = RobinhoodTrader.__new__(RobinhoodTrader)
    t.account_number = "ACCT"
    monkeypatch.setattr(t, "_ensure_auth", lambda: None, raising=False)
    sells, _, _ = plan_weekend_flatten([_pos()], [], FRI_1500, 15, 5)
    assert t.place_option_close_limit(sells[0]) == {"id": "abc", "state": "queued"}
    assert seen == {"positionEffect": "close", "creditOrDebit": "credit", "price": 0.75,
                    "symbol": "NBIS", "quantity": 17, "expirationDate": "2026-11-20",
                    "strike": 230.0, "optionType": "call", "account_number": "ACCT",
                    "timeInForce": "gfd", "jsonify": True}
    assert t.cancel_option_order("stop-1") is True and cancelled == ["stop-1"]
