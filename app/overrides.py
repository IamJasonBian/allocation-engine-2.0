"""Proxy mappings: always-on defaults, plus time-windowed earnings rows.

``default_overrides`` is the standing symbol → proxy map. ``custom_overrides``
is the earnings window: same shape with ``start_time`` / ``end_time``. A custom
row wins when ``at`` is in ``[start_time, end_time)``.

Engine-event names start with NBIS, MU, NVDA, QQQ, NET, SPY. Single names
proxy to QQQ; QQQ proxies to SPY. Windows are the next print (AMC = print
16:00 ET → next close; BMO = prior close → print close). QQQ/SPY share the
November 2026 tech-print week when NET/NBIS/NVDA report.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone


def _utc(year: int, month: int, day: int, hour: int = 21) -> datetime:
    """UTC timestamp. After 2026-11-01 US DST end, 21:00 UTC is 16:00 ET."""
    return datetime(year, month, day, hour, 0, tzinfo=timezone.utc)


@dataclass(frozen=True)
class DefaultOverride:
    symbol: str
    proxy: str


@dataclass(frozen=True)
class CustomOverride:
    symbol: str
    proxy: str
    start_time: datetime
    end_time: datetime


ENGINE_EVENT_TICKERS: tuple[str, ...] = (
    "NBIS", "MU", "NVDA", "QQQ", "NET", "SPY",
)

DEFAULT_OVERRIDES: tuple[DefaultOverride, ...] = (
    DefaultOverride(symbol="BTC", proxy="BTC/USD"),
    DefaultOverride(symbol="NBIS", proxy="QQQ"),
    DefaultOverride(symbol="MU", proxy="QQQ"),
    DefaultOverride(symbol="NVDA", proxy="QQQ"),
    DefaultOverride(symbol="QQQ", proxy="SPY"),
    DefaultOverride(symbol="NET", proxy="QQQ"),
    DefaultOverride(symbol="SPY", proxy="SPY"),
)

# Next earnings windows (as of 2026-10-20). QQQ/SPY cover the Nov print week.
CUSTOM_OVERRIDES: tuple[CustomOverride, ...] = (
    CustomOverride(  # NET Q3 FY26 AMC 2026-11-05 (est.)
        symbol="NET", proxy="QQQ",
        start_time=_utc(2026, 11, 5), end_time=_utc(2026, 11, 6),
    ),
    CustomOverride(  # NBIS Q3 FY26 BMO 2026-11-10 (est.)
        symbol="NBIS", proxy="QQQ",
        start_time=_utc(2026, 11, 9), end_time=_utc(2026, 11, 10),
    ),
    CustomOverride(  # NVDA Q3 FY27 AMC 2026-11-17 (confirmed)
        symbol="NVDA", proxy="QQQ",
        start_time=_utc(2026, 11, 17), end_time=_utc(2026, 11, 18),
    ),
    CustomOverride(  # MU Q1 FY27 AMC 2026-12-23 (est.)
        symbol="MU", proxy="QQQ",
        start_time=_utc(2026, 12, 23), end_time=_utc(2026, 12, 24),
    ),
    CustomOverride(  # index: Nov tech-print week (NET / NBIS / NVDA)
        symbol="QQQ", proxy="SPY",
        start_time=_utc(2026, 11, 5), end_time=_utc(2026, 11, 18),
    ),
    CustomOverride(
        symbol="SPY", proxy="SPY",
        start_time=_utc(2026, 11, 5), end_time=_utc(2026, 11, 18),
    ),
)


def resolve_proxy(symbol: str, at: datetime | None = None) -> str | None:
    """Return the active proxy for ``symbol``.

    A custom row wins when ``at`` is in ``[start_time, end_time)``. Otherwise
    the default row is used. Unknown symbols return None.
    """
    key = symbol.upper()
    when = at or datetime.now(timezone.utc)
    for row in CUSTOM_OVERRIDES:
        if row.symbol.upper() == key and row.start_time <= when < row.end_time:
            return row.proxy
    for row in DEFAULT_OVERRIDES:
        if row.symbol.upper() == key:
            return row.proxy
    return None
