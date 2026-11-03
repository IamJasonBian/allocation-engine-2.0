"""Option stop-loss planning, broker payload, and engine-loop gating."""

from app.risk.option_stop_loss import plan_option_stop_losses, snap_down, stop_prices
from app.utils_shared import maybe_option_stop_loss_sweep


def _pos(**over):
    p = {"chain_symbol": "MU", "option_type": "call", "position_type": "long",
         "strike": 1050.0, "expiration": "2026-11-20", "quantity": 2.0,
         "purchase_price": 20.65}
    p.update(over)
    return p


def _sell(qty=2, trigger="immediate", **leg):
    base = {"side": "sell", "position_effect": "close", "chain_symbol": "MU",
            "option_type": "call", "strike": 1050.0, "expiration": "2026-11-20"}
    base.update(leg)
    return {"state": "queued", "quantity": qty, "trigger": trigger, "legs": [base]}


def test_snap_down_uses_penny_program_ticks():
    assert snap_down(2.999) == 2.99
    assert snap_down(10.33) == 10.30
    assert snap_down(0.001) == 0.01


def test_stop_prices_trigger_and_limit():
    assert stop_prices(20.65, 50, 10) == (10.30, 9.25)
    assert stop_prices(1.00, 50, 10) == (0.50, 0.45)


def test_plans_stop_for_unprotected_long():
    orders, skipped = plan_option_stop_losses([_pos()], [], 50, 10)
    assert skipped == []
    assert orders == [{"contract": {"chain_symbol": "MU", "option_type": "call",
                                    "strike": 1050.0, "expiration": "2026-11-20"},
                       "quantity": 2, "entry": 20.65, "stop_pct": 50.0,
                       "stop_price": 10.30, "limit_price": 9.25}]


def test_skips_existing_stop_and_quantity_held_by_take_profit():
    _, skipped = plan_option_stop_losses([_pos()], [_sell(1, trigger="stop")], 50, 10)
    assert skipped[0]["reason"] == "stop_already_open"
    _, skipped = plan_option_stop_losses([_pos()], [_sell(2)], 50, 10)
    assert skipped[0]["reason"] == "held_by_open_sell"


def test_stops_only_unencumbered_quantity():
    orders, _ = plan_option_stop_losses([_pos(quantity=3.0)], [_sell(1)], 50, 10)
    assert orders[0]["quantity"] == 2


def test_ignores_filled_and_other_contract_orders():
    done = {**_sell(2), "state": "filled"}
    other = _sell(2, strike=1100.0)
    orders, _ = plan_option_stop_losses([_pos()], [done, other], 50, 10)
    assert orders[0]["quantity"] == 2


def test_skips_shorts_vertical_long_legs_and_missing_entry():
    vertical = [_pos(), _pos(position_type="short", strike=1100.0)]
    orders, skipped = plan_option_stop_losses(vertical, [], 50, 10)
    assert orders == [] and skipped[0]["reason"] == "paired_with_short_leg"
    _, skipped = plan_option_stop_losses([_pos(purchase_price=0)], [], 50, 10)
    assert skipped[0]["reason"] == "no_purchase_price"


class FakeBroker:
    def __init__(self):
        self.placed = []

    def place_option_stop_loss(self, order):
        self.placed.append(order)
        return {"id": f"oid-{len(self.placed)}", "state": "queued"}


def _cfg(**over):
    c = {"OPTION_SL_ENABLED": True, "OPTION_SL_SWEEP_DRY_RUN": True,
         "OPTION_SL_SWEEP_INTERVAL_SECONDS": 900, "OPTION_SL_PERCENT": 50,
         "OPTION_SL_LIMIT_OFFSET_PERCENT": 10}
    c.update(over)
    return c


def test_sweep_dry_run_places_nothing():
    b = FakeBroker()
    out = maybe_option_stop_loss_sweep(_cfg(), b, [_pos()], [], {})
    assert len(out["planned"]) == 1 and b.placed == []


def test_sweep_live_places_and_respects_interval(monkeypatch):
    posted = []
    monkeypatch.setattr("app.utils_shared._post_bot_activity",
                        lambda events, label: posted.extend(events))
    b, state = FakeBroker(), {}
    out = maybe_option_stop_loss_sweep(_cfg(OPTION_SL_SWEEP_DRY_RUN=False), b,
                                       [_pos()], [], state)
    assert len(out["placed"]) == 1 and len(b.placed) == 1
    assert posted[0]["type"] == "OPTION_STOP_LOSS"
    assert maybe_option_stop_loss_sweep(_cfg(OPTION_SL_SWEEP_DRY_RUN=False), b,
                                        [_pos()], [], state) is None
    assert len(b.placed) == 1


def test_sweep_disabled_or_unsupported_broker():
    assert maybe_option_stop_loss_sweep(_cfg(OPTION_SL_ENABLED=False),
                                        FakeBroker(), [_pos()], [], {}) is None
    assert maybe_option_stop_loss_sweep(_cfg(), object(), [_pos()], [], {}) is None


def test_robinhood_payload(monkeypatch):
    from app.brokers.robinhood_client import RobinhoodTrader

    seen = {}

    def fake(**kw):
        seen.update(kw)
        return {"id": "abc", "state": "queued"}

    monkeypatch.setattr("app.brokers.robinhood_client.rh.orders.order_sell_option_stop_limit", fake)
    t = RobinhoodTrader.__new__(RobinhoodTrader)
    t.account_number = "ACCT"
    monkeypatch.setattr(t, "_ensure_auth", lambda: None, raising=False)
    orders, _ = plan_option_stop_losses([_pos()], [], 50, 10)
    assert t.place_option_stop_loss(orders[0]) == {"id": "abc", "state": "queued"}
    assert seen == {"positionEffect": "close", "creditOrDebit": "credit",
                    "limitPrice": 9.25, "stopPrice": 10.30, "symbol": "MU",
                    "quantity": 2, "expirationDate": "2026-11-20", "strike": 1050.0,
                    "optionType": "call", "account_number": "ACCT",
                    "timeInForce": "gtc", "jsonify": True}
