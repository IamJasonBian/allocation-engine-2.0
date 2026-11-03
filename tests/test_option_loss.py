"""Tests for the option drawdown alert (app/risk/option_loss.py)."""

from app.enums import RiskEventType
from app.risk.option_loss import check_option_drawdowns


def _opt(purchase, mark, position_type="long"):
    return {
        "chain_symbol": "SPY", "option_type": "call", "position_type": position_type,
        "strike": 500.0, "expiration": "2026-10-17", "quantity": 2.0,
        "purchase_price": purchase, "mark_price": mark,
    }


def test_alerts_when_mark_more_than_25pct_below_purchase():
    events = check_option_drawdowns([_opt(2.00, 1.40)])
    assert len(events) == 1
    e = events[0]
    assert e.event_type == RiskEventType.OPTION_DRAWDOWN
    assert e.symbol == "SPY CALL 500.0 2026-10-17"
    assert round(e.drift_pct, 4) == 0.30


def test_exactly_25pct_does_not_alert():
    assert check_option_drawdowns([_opt(2.00, 1.50)]) == []


def test_short_positions_and_missing_purchase_are_skipped():
    assert check_option_drawdowns([_opt(2.00, 0.50, position_type="short")]) == []
    assert check_option_drawdowns([_opt(0, 0.50)]) == []


def test_telegram_observer_formats_drawdown(monkeypatch):
    from app.risk import slack_observer
    sent = []
    monkeypatch.setattr(slack_observer, "_notify", lambda text, **kw: sent.append(text))
    slack_observer.SlackAlertObserver().on_risk_event(
        check_option_drawdowns([_opt(2.00, 1.40)])[0])
    assert sent[0] == (
        "🐧 SPY CALL 500.0 2026-10-17 x2\n"
        "Buy $2.00 → Now $1.40 (-30%, $-120)"
    )


def test_telegram_observer_formats_mental_stop(monkeypatch):
    from datetime import date
    from app.risk import slack_observer
    from app.risk.mental_stops import check_mental_stops
    sent = []
    monkeypatch.setattr(slack_observer, "_notify",
                        lambda text, **kw: sent.append(text) or True)
    observer = slack_observer.SlackAlertObserver()
    ev, st = check_mental_stops([{**_opt(2.08, 0.95), "option_id": "spy-id", "mark_ok": True}],
                                {}, date(2026, 11, 3), send=observer.on_risk_event)
    assert len(ev) == 1 and st == {"spy-id": 1}
    assert sent[0] == (
        "🐧 SPY CALL 500.0 2026-10-17 x2\n"
        "Buy $2.08 → Now $0.95 (-54%, $-226)"
    )
