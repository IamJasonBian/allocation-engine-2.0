"""Earnings monitor — SHAPE ONLY, no implementation yet.

Runs on a feed the way `app/risk/` runs on prices: diff what the feed says
against what the store already holds, and publish the differences.

The diff is the whole job. A feed poll returns the same rows every time, so
emitting on every poll would alert continuously — the same trap the order-limit
events hit, fixed there by only reporting adjustments that changed shape.
"""

from __future__ import annotations

from abc import ABC, abstractmethod

from app.earnings.events import EarningsEvent

# Days before a report at which REPORT_IMMINENT fires.
DEFAULT_LEAD_DAYS = 3


class EarningsMonitor(ABC):
    """Turns feed rows into events by diffing against stored state."""

    @abstractmethod
    def poll(
        self,
        tickers: list[str] | None = None,
        lead_days: int = DEFAULT_LEAD_DAYS,
    ) -> list[EarningsEvent]:
        """Fetch, diff against the store, and return what changed.

        Args:
            tickers: symbols to check; None for the configured universe.
            lead_days: days ahead at which a report counts as imminent.

        Returns:
            Events for changes only — never one per row per poll.
        """
        raise NotImplementedError


# TODO(earnings-monitor): wire into the background loop.
#   - diff against EarningsStore, not against the last poll held in memory;
#     the process restarts and would re-alert the whole universe
#   - REPORT_RELEASED when a row flips upcoming -> reported
#   - ESTIMATE_REVISED when eps_estimate moves on a row that has not printed
#   - REPORT_IMMINENT is date-derived, so it must fire once, not every tick
#     inside the window
