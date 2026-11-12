"""Theta burn: math, alert thresholds, daily dedupe, and θ on tier alerts/summaries."""

from datetime import date

import pytest

from app.risk import alert_observer
from app.risk.mental_stops import check_mental_stops
from app.risk.theta_burn import (
    book_theta_text, burn_pct_of_value, daily_theta_burn, days_to_zero, net_theta_burn,
    theta_burn_alerts, theta_text,
)

D = date(2026, 11, 5)


def opt(buy=2.0, mark=1.0, theta=-0.10, qty=2.0, dte=20, short=False, strike=95.0,
        mult=100.0):
    return {"chain_symbol": "MU", "option_type": "call", "strike": strike,
            "expiration": "2026-11-25", "dte": dte, "quantity": qty, "multiplier": mult,
            "purchase_price": buy, "mark_price": mark,
            "position_type": "short" if short else "long",
            "greeks": {"theta": theta, "delta": None, "gamma": None, "vega": None, "iv": None}}


def alerts(pos, sent=frozenset(), today=D, pct=5, dte=7):
    return theta_burn_alerts(pos, set(sent), burn_pct=pct, dte_warn=dte, today=today)


# -- math ---------------------------------------------------------------------

def test_daily_burn_pct_and_days_to_zero():
    p = opt()                                         # |−0.10| × 2 × 100 = $20/day on $200
    assert daily_theta_burn(p) == pytest.approx(20.0)
    assert burn_pct_of_value(p) == pytest.approx(0.10)
    assert days_to_zero(p) == pytest.approx(10.0)


def test_uses_position_multiplier():
    assert daily_theta_burn(opt(mult=10)) == pytest.approx(2.0)


def test_missing_greeks_return_none():
    p = opt(theta=None)
    assert daily_theta_burn(p) is None and burn_pct_of_value(p) is None
    assert days_to_zero(p) is None
    p.pop("greeks")
    assert daily_theta_burn(p) is None


def test_zero_mark_has_burn_but_no_pct():
    p = opt(mark=0)
    assert daily_theta_burn(p) == pytest.approx(20.0)
    assert burn_pct_of_value(p) is None and days_to_zero(p) == 0


def test_shorts_are_skipped():
    p = opt(short=True)
    assert daily_theta_burn(p) is None and burn_pct_of_value(p) is None


def test_net_burn_for_a_vertical():
    long_leg, short_leg = opt(theta=-0.10), opt(theta=-0.04, short=True, strike=110.0)
    assert net_theta_burn(long_leg, short_leg) == pytest.approx(12.0)
    assert net_theta_burn(long_leg, opt(theta=None, short=True)) is None


def test_theta_text():
    assert theta_text(20) == "θ −$20.00/day"
    assert theta_text(-3) == "θ +$3.00/day"
    assert theta_text(None) == ""
    assert book_theta_text([opt(), opt(theta=None), opt(short=True)]) == "θ −$20.00/day"
    assert book_theta_text([opt(theta=None)]) == ""


# -- alert ----------------------------------------------------------------------

def test_alert_when_losing_and_burn_over_threshold():
    msgs, sent = alerts([opt()])                      # −50%, 10%/day, DTE 20
    assert msgs == ["🐧 Theta burn: MU CALL 95 2026-11-25 x2\n"
                    "P&L $-200 (-50%) · theta −$20.00/day (10.0% of value) · DTE 20"
                    " · zero in ~10d at this rate"]
    assert sent == {"2026-11-05|MU CALL 95 2026-11-25"}


def test_alert_when_losing_near_expiry_even_with_low_burn():
    msgs, _ = alerts([opt(theta=-0.01, dte=7)])        # 1%/day, DTE 7
    assert len(msgs) == 1 and "DTE 7" in msgs[0] and "zero in" not in msgs[0]


@pytest.mark.parametrize("p", [
    opt(mark=2.5, theta=-0.5, dte=1),                  # winning, even with heavy decay
    opt(theta=-0.01, dte=8),                           # 1%/day, DTE 8
    opt(theta=None, dte=1),                            # no greeks
    opt(short=True, mark=3.0, dte=1),                  # short
    opt(buy=0, dte=1),                                 # no cost basis
])
def test_no_alert(p):
    assert alerts([p])[0] == []


def test_threshold_is_inclusive():
    assert len(alerts([opt(theta=-0.05)])[0]) == 1     # exactly 5%/day


def test_once_per_contract_per_day():
    msgs, sent = alerts([opt()])
    for _ in range(3):
        msgs, sent = alerts([opt()], sent)
        assert msgs == []
    msgs, sent = alerts([opt()], sent, today=date(2026, 11, 6))
    assert len(msgs) == 1 and sent == {"2026-11-06|MU CALL 95 2026-11-25"}


# -- θ on tier alerts --------------------------------------------------------------

def test_tier_alert_carries_theta(monkeypatch):
    sent = []
    monkeypatch.setattr(alert_observer, "_notify", lambda text, **kw: sent.append(text))
    ev, _ = check_mental_stops([opt(mark=1.1)], {}, D)
    assert ev[0].metadata["theta_usd"] == pytest.approx(20.0)
    alert_observer.AlertObserver().on_risk_event(ev[0])
    assert sent[0].endswith(" · θ −$20.00/day")


def test_tier_alert_without_theta_is_unchanged(monkeypatch):
    sent = []
    monkeypatch.setattr(alert_observer, "_notify", lambda text, **kw: sent.append(text))
    ev, _ = check_mental_stops([opt(mark=1.1, theta=None)], {}, D)
    alert_observer.AlertObserver().on_risk_event(ev[0])
    assert "θ" not in sent[0]
