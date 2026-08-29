"""Earnings feed interface — SHAPE ONLY, no implementation yet.

`app/earnings_writer.fetch_yfinance` is the one concrete implementation today.
This pins the signature so a paid vendor drops in beside it without the store,
the trim, or the monitor changing.

Field mappings for the candidates (all are already per-quarter estimate/actual
rows, so only names and the date key differ):

    Twelve Data /earnings        date, eps_estimate, eps_actual, surprise
    FMP /earnings-surprises      date, epsEstimated, epsActual
    Finnhub /stock/earnings      period, estimate, actual, surprisePercent
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

# The vendor-neutral record every feed must emit.
RECORD_FIELDS = ("ticker", "earnings_date", "eps_estimate", "eps_actual", "status")


@runtime_checkable
class EarningsFeed(Protocol):
    """A source of per-quarter EPS rows."""

    name: str

    def fetch(
        self,
        tickers: list[str],
        quarters: int = 12,
        as_of: str | None = None,
    ) -> tuple[list[dict], list[dict]]:
        """Pull EPS rows.

        A failing symbol must be returned, never raised — a partial refresh is
        still worth persisting.

        Args:
            tickers: symbols to fetch.
            quarters: quarters per ticker before trimming.
            as_of: ISO date splitting past from future.

        Returns:
            (records, failures); records carry RECORD_FIELDS.
        """
        ...


# TODO(vendor): add TwelveDataFeed / FinnhubFeed beside the yfinance path.
#   Yahoo has no SLA and silently drops figures for older quarters, which is
#   why `earnings_writer.classify` has to treat past+null as 'missing'.
