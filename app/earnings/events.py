"""Earnings events — SHAPE ONLY, no implementation yet.

Mirrors `app/risk/events.py`: an immutable record something publishes onto a
bus. Kept separate from RiskEvent because an earnings event carries a quarter
and an estimate rather than a drift percentage, and `RiskEvent.severity` keys
off drift_pct — reusing it would force a meaningless number into that field.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import StrEnum


class EarningsEventType(StrEnum):
    """What the feed just told us."""

    REPORT_SCHEDULED = "report_scheduled"   # a date appeared or moved
    REPORT_IMMINENT = "report_imminent"     # inside the configured lead window
    REPORT_RELEASED = "report_released"     # an actual landed
    ESTIMATE_REVISED = "estimate_revised"   # consensus moved before the print


@dataclass(frozen=True)
class EarningsEvent:
    """One thing that happened to a ticker's earnings state."""

    event_type: EarningsEventType
    ticker: str
    earnings_date: str                      # ISO
    message: str
    eps_estimate: float | None = None
    eps_actual: float | None = None
    previous_estimate: float | None = None  # set on ESTIMATE_REVISED
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    metadata: dict = field(default_factory=dict)

    @property
    def surprise(self) -> float | None:
        """Actual minus estimate in dollars, or None if either side is missing.

        Dollars, not percent: percent detonates near zero consensus, which is
        common for this cohort (a $0.01 estimate printing $0.02 is +100%).
        """
        if self.eps_actual is None or self.eps_estimate is None:
            return None
        return self.eps_actual - self.eps_estimate
