"""Option drawdown — pure breach detection (ruleset ``drawdown`` adds dedupe)."""

from __future__ import annotations

import logging

from app.enums import RiskEventType
from app.risk.events import RiskEvent

log = logging.getLogger(__name__)

OPTION_DRAWDOWN_THRESHOLD = 0.25


def option_key(pos: dict) -> str:
    """Stable identifier for an option contract, e.g. ``SPY CALL 500.0 2026-10-17``."""
    return (
        f"{pos.get('chain_symbol', '?')} {str(pos.get('option_type', '?')).upper()} "
        f"{pos.get('strike', '?')} {pos.get('expiration', '?')}"
    )


def decide_drawdown_events(options_positions: list[dict]) -> list[RiskEvent]:
    """Return an OPTION_DRAWDOWN event per long option whose mark is >25% below purchase."""
    events: list[RiskEvent] = []
    for pos in options_positions:
        if pos.get("position_type") == "short":
            continue
        purchase = float(pos.get("purchase_price") or 0)
        mark = pos.get("mark_price")
        if purchase <= 0 or mark is None:
            continue
        drop = (purchase - float(mark)) / purchase
        if drop <= OPTION_DRAWDOWN_THRESHOLD:
            continue

        key = option_key(pos)
        event = RiskEvent(
            event_type=RiskEventType.OPTION_DRAWDOWN,
            symbol=key,
            drift_pct=drop,
            message=(
                f"{key} mark ${float(mark):.2f} is {drop:.2%} below "
                f"purchase ${purchase:.2f} (qty {pos.get('quantity')})"
            ),
            metadata={
                "purchase_price": purchase,
                "mark_price": float(mark),
                "quantity": pos.get("quantity"),
            },
        )
        events.append(event)
    return events


def check_option_drawdowns(options_positions: list[dict]) -> list[RiskEvent]:
    """Backward-compatible alias for ``decide_drawdown_events`` (no dedupe)."""
    events = decide_drawdown_events(options_positions)
    for event in events:
        log.warning("OPTION DRAWDOWN: %s", event.message)
    return events
