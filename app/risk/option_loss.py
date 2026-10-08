"""Option drawdown check — alert when a long option's mark falls far below purchase."""

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


def check_option_drawdowns(options_positions: list[dict]) -> list[RiskEvent]:
    """Return an OPTION_DRAWDOWN event per long option whose mark is >25% below purchase.

    Args:
        options_positions: Option position dicts from ``broker.options_positions()``.

    Returns:
        One RiskEvent per breaching position (empty when none breach).
    """
    events: list[RiskEvent] = []
    for pos in options_positions:
        # Short premium loses when the mark rises — not what this alert covers.
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
        log.warning("OPTION DRAWDOWN: %s", event.message)
        events.append(event)
    return events
