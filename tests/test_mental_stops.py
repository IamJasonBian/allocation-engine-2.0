"""Alert-only P&L mental stops over open option positions."""

from datetime import date

from app.enums import RiskEventType
from app.risk.mental_stops import check_mental_stops, group_positions

D = date(2026, 11, 5)


def opt(sym, strike, buy, mark, qty=1.0, short=False, exp="2026-11-20", kind="call"):
    return {"chain_symbol": sym, "option_type": kind, "strike": strike, "expiration": exp,
            "quantity": qty, "purchase_price": buy, "mark_price": mark,
            "position_type": "short" if short else "long"}


def test_single_long_tiers_alert_once_each():
    pos = [opt("NBIS", 230, 2.08, 1.20)]               # -42%
    ev, st = check_mental_stops(pos, {}, D)
    assert len(ev) == 1 and ev[0].event_type == RiskEventType.OPTION_MENTAL_STOP
    assert ev[0].metadata["tier"] == 0.40
    ev, st = check_mental_stops(pos, st, D)            # same level: silent
    assert ev == []
    deeper = [opt("NBIS", 230, 2.08, 0.70)]            # -66%
    ev, st = check_mental_stops(deeper, st, D)
    assert len(ev) == 1 and ev[0].metadata["tier"] == 0.60
    assert ev[0].severity == "critical"


def test_recovery_rearms_the_tier():
    _, st = check_mental_stops([opt("NBIS", 230, 2.0, 1.1)], {}, D)         # -45%
    ev, st = check_mental_stops([opt("NBIS", 230, 2.0, 1.9)], st, D)        # recovered
    assert ev == []
    ev, _ = check_mental_stops([opt("NBIS", 230, 2.0, 1.1)], st, D)
    assert len(ev) == 1


def test_vertical_spread_is_one_position():
    pos = [opt("AMD", 470, 19.65, 19.65), opt("AMD", 510, 6.85, 6.85, short=True)]
    units = group_positions(pos)
    assert len(units) == 1 and units[0]["key"].endswith("470/510 2026-11-20 SPREAD")
    assert round(units[0]["cost"], 2) == 12.80
    # AMD falls: long 5.00, short 0.50 -> spread 4.50, -65% of 12.80
    ev, _ = check_mental_stops([opt("AMD", 470, 19.65, 5.0),
                                opt("AMD", 510, 6.85, 0.5, short=True)], {}, D)
    assert len(ev) == 1 and "SPREAD" in ev[0].symbol and ev[0].metadata["tier"] == 0.60


def test_profit_and_flat_do_not_alert():
    ev, _ = check_mental_stops([opt("NBIS", 230, 2.0, 3.0), opt("MU", 900, 5, 5)], {}, D)
    assert ev == []


def test_off_after_cutoff_and_never_trades():
    ev, st = check_mental_stops([opt("NBIS", 230, 2.0, 0.2)], {"x": 1}, date(2026, 11, 24))
    assert ev == [] and st == {"x": 1}


def test_pnl_digest_slots_once_per_day_and_lists_every_position():
    from datetime import datetime
    from zoneinfo import ZoneInfo
    from app.risk.mental_stops import pnl_digest
    et = ZoneInfo("America/New_York")
    pos = [opt("NBIS", 230, 2.08, 0.955, qty=31), opt("NBIS", 230, 3.42, 2.125, qty=3, exp="2026-12-04")]
    assert pnl_digest(pos, set(), datetime(2026, 11, 3, 9, 0, tzinfo=et))[0] is None     # before first slot
    msg, sent = pnl_digest(pos, set(), datetime(2026, 11, 3, 9, 40, tzinfo=et))
    assert msg.count("NBIS CALL 230") == 2 and "Total" in msg
    assert pnl_digest(pos, sent, datetime(2026, 11, 3, 11, 0, tzinfo=et))[0] is None    # already sent
    assert pnl_digest(pos, sent, datetime(2026, 11, 3, 15, 50, tzinfo=et))[0] is not None  # second slot
    assert pnl_digest(pos, set(), datetime(2026, 11, 7, 10, 0, tzinfo=et))[0] is None    # Saturday
