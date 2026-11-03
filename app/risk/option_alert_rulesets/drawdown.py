"""Ruleset ``drawdown`` — long single-leg option loss vs purchase.

When it fires
-------------
- Each **long** option leg (not shorts, not spread-netting).
- ``(purchase_price - mark_price) / purchase_price > 25%`` (strictly above 25%).
- Requires positive ``purchase_price`` and a ``mark_price``.

Dedupe
------
- One Telegram alert the first poll a contract enters breach.
- No repeat while it stays in breach.
- Re-arms after mark recovers (no longer breaching), then can alert again.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

from app.enums import RiskEventType
from app.risk.events import RiskEvent
from app.risk.option_loss import OPTION_DRAWDOWN_THRESHOLD, decide_drawdown_events, option_key

log = logging.getLogger(__name__)


@dataclass
class DrawdownState:
    """Keys currently in drawdown breach (already notified this episode)."""

    breaching: set[str] = field(default_factory=set)


class DrawdownRuleset:
    code = "drawdown"

    def initial_state(self) -> DrawdownState:
        return DrawdownState()

    def tick(
        self,
        options_positions: list[dict],
        state: DrawdownState,
        *,
        today=None,
    ) -> tuple[list[RiskEvent], DrawdownState]:
        del today  # drawdown has no calendar cutoff
        breaches = decide_drawdown_events(options_positions)
        breaching_now = {e.symbol for e in breaches}
        events: list[RiskEvent] = []
        for event in breaches:
            if event.symbol not in state.breaching:
                meta = dict(event.metadata)
                meta["ruleset"] = self.code
                events.append(
                    RiskEvent(
                        event_type=event.event_type,
                        symbol=event.symbol,
                        drift_pct=event.drift_pct,
                        message=event.message,
                        metadata=meta,
                    )
                )
                log.warning("option alert [%s]: %s", self.code, event.message)
        return events, DrawdownState(breaching=breaching_now)


# Re-export for tests/docs
__all__ = ["DrawdownRuleset", "DrawdownState", "OPTION_DRAWDOWN_THRESHOLD", "option_key"]
