"""Alert-only P&L mental stops over open option positions."""

from datetime import date

from app.enums import RiskEventType
from app.risk.mental_stops import check_mental_stops, group_positions

D = date(2026, 11, 5)


def opt(sym, strike, buy, mark, qty=1.0, short=False, exp="2026-11-20", kind="call",
        oid=None, mark_ok=True):
    return {"option_id": oid or f"{sym}-{strike}", "mark_ok": mark_ok,
            "chain_symbol": sym, "option_type": kind, "strike": strike, "expiration": exp,
            "quantity": qty, "purchase_price": buy, "mark_price": mark,
            "position_type": "short" if short else "long"}


def ok(_e):
    return True


def check(pos, st, today=D, send=ok):
    return check_mental_stops(pos, st, today, send=send)


def test_single_long_tiers_alert_once_each():
    pos = [opt("NBIS", 230, 2.08, 1.20)]               # -42%
    ev, st = check(pos, {})
    assert len(ev) == 1 and ev[0].event_type == RiskEventType.OPTION_MENTAL_STOP
    assert ev[0].metadata["tier"] == 0.40
    ev, st = check(pos, st)                            # same level: silent
    assert ev == []
    deeper = [opt("NBIS", 230, 2.08, 0.70)]            # -66%
    ev, st = check(deeper, st)
    assert len(ev) == 1 and ev[0].metadata["tier"] == 0.60
    assert ev[0].severity == "critical"


def test_recovery_rearms_the_tier():
    _, st = check([opt("NBIS", 230, 2.0, 1.1)], {})         # -45%
    ev, st = check([opt("NBIS", 230, 2.0, 1.9)], st)        # recovered
    assert ev == []
    ev, _ = check([opt("NBIS", 230, 2.0, 1.1)], st)
    assert len(ev) == 1


def test_vertical_spread_is_one_position():
    pos = [opt("AMD", 470, 19.65, 19.65, oid="L"), opt("AMD", 510, 6.85, 6.85, short=True, oid="S")]
    units = group_positions(pos)
    assert len(units) == 1 and units[0]["key"] == "L/S"
    assert units[0]["label"].endswith("470/510 2026-11-20 SPREAD")
    assert round(units[0]["cost"], 2) == 12.80
    # AMD falls: long 5.00, short 0.50 -> spread 4.50, -65% of 12.80
    ev, _ = check([opt("AMD", 470, 19.65, 5.0), opt("AMD", 510, 6.85, 0.5, short=True)], {})
    assert len(ev) == 1 and "SPREAD" in ev[0].symbol and ev[0].metadata["tier"] == 0.60


def test_profit_and_flat_do_not_alert():
    ev, _ = check([opt("NBIS", 230, 2.0, 3.0), opt("MU", 900, 5, 5)], {})
    assert ev == []


def test_off_after_cutoff_and_never_trades():
    ev, st = check([opt("NBIS", 230, 2.0, 0.2)], {"x": 1}, date(2026, 11, 24))
    assert ev == [] and st == {"x": 1}


def test_first_tier_is_minus_25_and_one_alert_per_level():
    _, st = check([opt("NBIS", 230, 3.42, 2.40, oid="nbis-id")], {})   # -30%
    assert st["nbis-id"] == 0
    ev, _ = check([opt("NBIS", 230, 3.42, 2.40, oid="nbis-id")], st)
    assert ev == []                                                     # no repeat
    ev, _ = check([opt("NBIS", 230, 3.42, 2.40, oid="nbis-id")], {})
    assert len(ev) == 1 and ev[0].metadata["tier"] == 0.25


def test_mark_fallback_does_not_alert():
    ev, st = check([opt("NBIS", 230, 2.0, 0.2, mark_ok=False)], {})
    assert ev == [] and st == {}
    spread = [opt("AMD", 470, 19.65, 5.0), opt("AMD", 510, 6.85, 0.5, short=True, mark_ok=False)]
    assert group_positions(spread) == []


def test_failed_send_re_alerts_next_pass():
    pos = [opt("NBIS", 230, 2.0, 1.1, oid="n")]                         # -45%
    ev, st = check(pos, {}, send=lambda _e: False)
    assert len(ev) == 1 and st.get("n", -1) == -1
    ev, st = check(pos, st)
    assert len(ev) == 1 and st["n"] == 1
    ev, _ = check(pos, st)
    assert ev == []


def test_state_survives_a_pass_with_missing_data():
    pos = [opt("NBIS", 230, 2.0, 1.1, oid="n")]
    _, st = check(pos, {})
    _, st = check([], st)                                     # position left out this pass
    _, st = check([opt("NBIS", 230, 2.0, 1.1, oid="n", mark_ok=False)], st)
    assert st["n"] == 1
    ev, _ = check(pos, st)
    assert ev == []
