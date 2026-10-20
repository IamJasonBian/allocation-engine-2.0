"""Proxy mappings: always-on defaults, plus time-windowed earnings rows.

``default_overrides`` is the standing symbol → proxy map. ``custom_overrides``
is the earnings window: same shape with ``start_time`` / ``end_time``. A custom
row wins when ``at`` is in ``[start_time, end_time)``.

Engine-event names start with NBIS, MU, NVDA, QQQ, NET, SPY. Single names
proxy to QQQ; QQQ proxies to SPY. Each name has the next two prints hardcoded.
AMC = print 16:00 ET → next close; BMO = prior close → print close.

TODO: replace the hardcoded windows with TwelveData
  GET https://api.twelvedata.com/earnings?symbol=NVDA&period=next
  (date + Pre Market / After Hours). Allow-list ``earnings`` on the existing
  5thstreetcapital twelvedata proxy instead of holding a second key.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

_ET = ZoneInfo("America/New_York")


def _et_close(year: int, month: int, day: int) -> datetime:
    """16:00 America/New_York, stored as UTC."""
    return datetime(year, month, day, 16, 0, tzinfo=_ET).astimezone(timezone.utc)


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

# Hardcoded next two prints (as of 2026-10-20). See module TODO for the fetch.
CUSTOM_OVERRIDES: tuple[CustomOverride, ...] = (
    # NET: 2026-11-05 AMC (confirmed), 2027-02-11 AMC (est.)
    CustomOverride("NET", "QQQ", _et_close(2026, 11, 5), _et_close(2026, 11, 6)),
    CustomOverride("NET", "QQQ", _et_close(2027, 2, 11), _et_close(2027, 2, 12)),
    # NBIS: 2026-11-10 BMO (est.), 2027-02-11 BMO (est.)
    CustomOverride("NBIS", "QQQ", _et_close(2026, 11, 9), _et_close(2026, 11, 10)),
    CustomOverride("NBIS", "QQQ", _et_close(2027, 2, 10), _et_close(2027, 2, 11)),
    # NVDA: 2026-11-17 AMC (confirmed), 2027-02-24 AMC (est.)
    CustomOverride("NVDA", "QQQ", _et_close(2026, 11, 17), _et_close(2026, 11, 18)),
    CustomOverride("NVDA", "QQQ", _et_close(2027, 2, 24), _et_close(2027, 2, 25)),
    # MU: 2026-12-23 AMC (est.), 2027-03-24 AMC (est.)
    CustomOverride("MU", "QQQ", _et_close(2026, 12, 23), _et_close(2026, 12, 24)),
    CustomOverride("MU", "QQQ", _et_close(2027, 3, 24), _et_close(2027, 3, 25)),
    # QQQ/SPY: next two book clusters (Nov tech week, then Feb tech week)
    CustomOverride("QQQ", "SPY", _et_close(2026, 11, 5), _et_close(2026, 11, 18)),
    CustomOverride("QQQ", "SPY", _et_close(2027, 2, 11), _et_close(2027, 2, 25)),
    CustomOverride("SPY", "SPY", _et_close(2026, 11, 5), _et_close(2026, 11, 18)),
    CustomOverride("SPY", "SPY", _et_close(2027, 2, 11), _et_close(2027, 2, 25)),
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
