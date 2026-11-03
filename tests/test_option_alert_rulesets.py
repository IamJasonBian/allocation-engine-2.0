"""Registry and drawdown ruleset dedupe."""

from datetime import date

from app.risk.option_alert_rulesets import build_registry, run_tick
from app.risk.option_alert_rulesets.registry import RegistryError


def test_unknown_enabled_ruleset_raises():
    try:
        build_registry(frozenset({"drawdown", "nope"}))
    except RegistryError as e:
        assert "nope" in str(e)
    else:
        raise AssertionError("expected RegistryError")


def test_drawdown_dedupes_within_breach():
    from app.risk.option_loss import check_option_drawdowns

    pos = [{
        "chain_symbol": "SPY", "option_type": "call", "position_type": "long",
        "strike": 500.0, "expiration": "2026-10-17", "quantity": 2.0,
        "purchase_price": 2.00, "mark_price": 1.40,
    }]
    reg = build_registry(frozenset({"drawdown"}))
    states = {}
    ev1, states = run_tick(pos, states, reg)
    ev2, states = run_tick(pos, states, reg)
    assert len(ev1) == 1 and ev2 == []
    assert ev1[0].metadata.get("ruleset") == "drawdown"
    ev3, states = run_tick([], states, reg)
    assert ev3 == []
    ev4, _ = run_tick(pos, states, reg)
    assert len(ev4) == 1


def test_mental_stop_runs_when_enabled():
    reg = build_registry(frozenset({"mental_stop"}))
    pos = [{
        "chain_symbol": "NBIS", "option_type": "call", "strike": 230,
        "expiration": "2026-11-20", "quantity": 1.0,
        "purchase_price": 2.08, "mark_price": 0.95,
    }]
    events, _ = run_tick(pos, {}, reg, today=date(2026, 11, 3))
    assert len(events) == 1 and events[0].metadata.get("ruleset") == "mental_stop"
