"""Ruleset ``mental_stop`` — combined spread or long P&L tiers.

When it fires
-------------
- **Units:** single long legs, or long+short verticals on same symbol/type/exp
  with matching qty (net debit cost and net mark).
- **Tiers:** loss vs cost crosses 40%, 60%, or 80% (``loss = (cost-mark)/cost``).
- **Calendar:** only while ``today <= OPTION_MENTAL_STOPS_UNTIL`` (default 2026-11-23).

Dedupe
------
- One alert per tier per position key; deeper tier fires when loss worsens.
- Re-arm a tier after mark recovers by ``REARM_BAND`` (10 pts) below that tier.
"""

from __future__ import annotations

import logging
from typing import Any

from app.risk.mental_stops import check_mental_stops

log = logging.getLogger(__name__)


class MentalStopRuleset:
    code = "mental_stop"

    def initial_state(self) -> dict[str, int]:
        return {}

    def tick(
        self,
        options_positions: list[dict],
        state: dict[str, int],
        *,
        today=None,
    ) -> tuple[list[Any], dict[str, int]]:
        from app.risk.events import RiskEvent

        events, new_state = check_mental_stops(options_positions, state, today=today)
        out: list[RiskEvent] = []
        for event in events:
            meta = dict(event.metadata)
            meta["ruleset"] = self.code
            out.append(
                RiskEvent(
                    event_type=event.event_type,
                    symbol=event.symbol,
                    drift_pct=event.drift_pct,
                    message=event.message,
                    metadata=meta,
                )
            )
            log.warning("option alert [%s]: %s", self.code, event.message.splitlines()[0])
        return out, new_state
