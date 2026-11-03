"""Alert coverage for every open option: new/gone, unmonitored, and the summary line."""

from datetime import date, datetime
from zoneinfo import ZoneInfo

from app.risk.eod_summary import pending_summaries_due
from app.risk.option_coverage import (
    coverage_line, position_change_alerts, unmonitored_alerts, unmonitored_reason,
)

D = date(2026, 11, 3)


def opt(sym="MU", strike=95.0, buy=2.0, mark=2.5, qty=2.0, short=False, **over):
    p = {"chain_symbol": sym, "option_type": "call", "strike": strike,
         "expiration": "2026-11-20", "dte": 17, "quantity": qty,
         "purchase_price": buy, "mark_price": mark,
         "position_type": "short" if short else "long"}
    p.update(over)
    return p


def order(strike=95.0, side="sell", state="queued", effect="close"):
    return {"state": state, "quantity": 1, "symbol": "MU",
            "legs": [{"side": side, "position_effect": effect, "chain_symbol": "MU",
                      "option_type": "call", "strike": strike, "expiration": "2026-11-20"}]}


# -- new / gone ---------------------------------------------------------------

def test_first_pass_records_book_without_alerting():
    msgs, seen = position_change_alerts([opt()], None)
    assert msgs == [] and seen == {"MU CALL 95 2026-11-20": 2.0}


def test_new_position_alerts_once_with_buy_now_and_dte():
    _, seen = position_change_alerts([], None)
    msgs, seen = position_change_alerts([opt()], seen)
    assert msgs == ["🐧 New option position: MU CALL 95 2026-11-20 x2\n"
                    "Buy $2.00 → Now $2.50 · DTE 17"]
    for _ in range(3):                                     # later passes: silent
        msgs, seen = position_change_alerts([opt()], seen)
        assert msgs == []


def test_gone_position_alerts_once():
    _, seen = position_change_alerts([opt(), opt(strike=100.0)], None)
    msgs, seen = position_change_alerts([opt()], seen)
    assert msgs == ["🐧 Option position gone (closed or expired): MU CALL 100 2026-11-20 x2"]
    msgs, _ = position_change_alerts([opt()], seen)
    assert msgs == []


def test_short_is_a_distinct_contract():
    _, seen = position_change_alerts([opt()], None)
    msgs, _ = position_change_alerts([opt(), opt(short=True)], seen)
    assert len(msgs) == 1 and "MU CALL 95 2026-11-20 SHORT" in msgs[0]


def test_empty_read_is_not_a_close_out():
    _, seen = position_change_alerts([opt()], None)
    msgs, seen2 = position_change_alerts([], seen)
    assert msgs == [] and seen2 == seen


def test_incomplete_lot_does_not_report_others_closed():
    _, seen = position_change_alerts([opt(), opt(strike=100.0)], None)
    broken = opt(strike=0.0, expiration="")                # instrument lookup failed
    msgs, seen = position_change_alerts([opt(), broken], seen)
    assert msgs == [] and "MU CALL 100 2026-11-20" in seen


# -- unmonitored ---------------------------------------------------------------

def test_unmonitored_reasons():
    assert unmonitored_reason(opt()) is None
    assert unmonitored_reason(opt(buy=0)) == "no purchase price"
    assert unmonitored_reason(opt(mark=None)) == "no mark price"
    assert "fallback" in unmonitored_reason(opt(mark=2.0))
    assert unmonitored_reason(opt(mark=2.0, mark_ok=True)) is None
    assert "fell back" in unmonitored_reason(opt(mark_ok=False))


def test_unmonitored_alerts_once_per_contract_per_day():
    pos = [opt(mark=None), opt(strike=100.0)]
    msgs, sent = unmonitored_alerts(pos, set(), D)
    assert len(msgs) == 1 and msgs[0].startswith("🐧 Not monitored: MU CALL 95")
    assert "no mark price" in msgs[0]
    for _ in range(3):
        msgs, sent = unmonitored_alerts(pos, sent, D)
        assert msgs == []
    msgs, sent = unmonitored_alerts(pos, sent, date(2026, 11, 4))   # next day: again
    assert len(msgs) == 1 and sent == {"2026-11-04|MU CALL 95 2026-11-20"}


# -- coverage line --------------------------------------------------------------

def test_coverage_line_counts():
    pos = [opt(), opt(strike=100.0, mark=None), opt(strike=110.0, short=True),
           opt(strike=120.0)]
    orders = [order(95.0), order(110.0, side="buy"), order(120.0, state="cancelled"),
              order(100.0, effect="open")]
    assert coverage_line(pos, orders) == (
        "options: 4 open · 3 monitored · 1 unmonitored · 2 with exit orders (TP/SL)")


def test_coverage_line_unknown_exits_when_orders_unreadable():
    assert coverage_line([opt()], None).endswith("· ? with exit orders (TP/SL)")


def test_coverage_line_is_in_every_summary():
    at = datetime(2026, 11, 3, 17, 0, tzinfo=ZoneInfo("America/New_York"))
    line = coverage_line([opt()], [])
    msgs, _ = pending_summaries_due([], set(), now=at, include_startup=True, coverage=line)
    assert len(msgs) == 3 and all(m.endswith(line) for m in msgs)
