"""Market-session helper for gating live order submission to safe RTH windows.

The single public entry point is :func:`is_tradeable`, which decides whether a
given Eastern-Time instant falls inside the tradeable window for US equity /
listed-options regular trading hours (RTH), accounting for weekends, holidays,
and early-close half-days (1:00 PM ET).

`now_et` is an injectable parameter so callers control the clock — the module
keeps no hidden state and is fully deterministic under test.

Calendar source preference:
1. ``pandas_market_calendars`` (XNYS) when importable — authoritative holidays
   and half-days.
2. A self-contained ``datetime`` fallback covering regular RTH, weekends, and
   the common US market holidays + half-days. The fallback only inspects the
   local wall-clock date, so it works even without ``pytz`` installed.
"""

from __future__ import annotations

import datetime as _dt
import logging

log = logging.getLogger(__name__)

# Regular trading hours in ET.
_RTH_OPEN = _dt.time(9, 30)
_RTH_CLOSE = _dt.time(16, 0)
# Early-close (half-day) close in ET.
_HALF_DAY_CLOSE = _dt.time(13, 0)

try:  # pragma: no cover - exercised only when the dep is installed
    import pandas_market_calendars as _mcal

    _XNYS = _mcal.get_calendar("XNYS")
except Exception:  # pragma: no cover - fallback path
    _mcal = None
    _XNYS = None


def _nth_weekday(year: int, month: int, weekday: int, n: int) -> _dt.date:
    """Return the date of the ``n``-th ``weekday`` (Mon=0) in ``month``/``year``."""
    first = _dt.date(year, month, 1)
    offset = (weekday - first.weekday()) % 7
    return first + _dt.timedelta(days=offset + 7 * (n - 1))


def _last_weekday(year: int, month: int, weekday: int) -> _dt.date:
    """Return the date of the last ``weekday`` (Mon=0) in ``month``/``year``."""
    if month == 12:
        nxt = _dt.date(year + 1, 1, 1)
    else:
        nxt = _dt.date(year, month + 1, 1)
    last = nxt - _dt.timedelta(days=1)
    return last - _dt.timedelta(days=(last.weekday() - weekday) % 7)


def _observed(d: _dt.date) -> _dt.date:
    """Apply the US weekend-observation rule to a fixed-date holiday."""
    if d.weekday() == 5:  # Saturday -> observed Friday
        return d - _dt.timedelta(days=1)
    if d.weekday() == 6:  # Sunday -> observed Monday
        return d + _dt.timedelta(days=1)
    return d


def _good_friday(year: int) -> _dt.date:
    """Compute Good Friday (anonymous Gregorian / Meeus algorithm for Easter)."""
    a = year % 19
    b = year // 100
    c = year % 100
    d = b // 4
    e = b % 4
    f = (b + 8) // 25
    g = (b - f + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i = c // 4
    k = c % 4
    m = (32 + 2 * e + 2 * i - h - k) % 7
    n = (a + 11 * h + 22 * m) // 451
    month = (h + m - 7 * n + 114) // 31
    day = ((h + m - 7 * n + 114) % 31) + 1
    easter = _dt.date(year, month, day)
    return easter - _dt.timedelta(days=2)


def _full_holidays(year: int) -> set[_dt.date]:
    """US market full-closure holidays for a given year (fallback calendar)."""
    return {
        _observed(_dt.date(year, 1, 1)),    # New Year's Day
        _nth_weekday(year, 1, 0, 3),        # MLK Day (3rd Mon Jan)
        _nth_weekday(year, 2, 0, 3),        # Presidents' Day (3rd Mon Feb)
        _good_friday(year),                 # Good Friday
        _last_weekday(year, 5, 0),          # Memorial Day (last Mon May)
        _observed(_dt.date(year, 6, 19)),   # Juneteenth
        _observed(_dt.date(year, 7, 4)),    # Independence Day
        _nth_weekday(year, 9, 0, 1),        # Labor Day (1st Mon Sep)
        _nth_weekday(year, 11, 3, 4),       # Thanksgiving (4th Thu Nov)
        _observed(_dt.date(year, 12, 25)),  # Christmas
    }


def _half_days(year: int) -> set[_dt.date]:
    """US market early-close half-days for a given year (fallback calendar)."""
    days: set[_dt.date] = set()
    # Day after Thanksgiving (Black Friday).
    days.add(_nth_weekday(year, 11, 3, 4) + _dt.timedelta(days=1))
    # Christmas Eve, when it falls on a weekday and Christmas is on the 25th.
    xmas_eve = _dt.date(year, 12, 24)
    if xmas_eve.weekday() < 5:
        days.add(xmas_eve)
    # July 3rd, when it falls on a weekday and the 4th is the observed holiday.
    july3 = _dt.date(year, 7, 3)
    if july3.weekday() < 5 and _observed(_dt.date(year, 7, 4)) == _dt.date(year, 7, 4):
        days.add(july3)
    return days


def _session_bounds_fallback(d: _dt.date) -> tuple[_dt.time, _dt.time] | None:
    """Return (open, close) ET times for a date, or None if the market is closed."""
    if d.weekday() >= 5:
        return None
    if d in _full_holidays(d.year):
        return None
    if d in _half_days(d.year):
        return _RTH_OPEN, _HALF_DAY_CLOSE
    return _RTH_OPEN, _RTH_CLOSE


def _session_bounds_mcal(d: _dt.date) -> tuple[_dt.time, _dt.time] | None:
    """Return (open, close) ET times for a date using pandas_market_calendars."""
    sched = _XNYS.schedule(start_date=d.isoformat(), end_date=d.isoformat())
    if sched.empty:
        return None
    et = "America/New_York"
    open_ts = sched.iloc[0]["market_open"].tz_convert(et)
    close_ts = sched.iloc[0]["market_close"].tz_convert(et)
    return open_ts.time(), close_ts.time()


def is_tradeable(
    now_et: _dt.datetime,
    *,
    open_buffer_min: int,
    close_buffer_min: int,
) -> tuple[bool, str]:
    """Decide whether ``now_et`` is inside the tradeable RTH window.

    Args:
        now_et: The current instant expressed in US/Eastern. May be naive (assumed
            ET) or tz-aware — only the local wall-clock date/time is used.
        open_buffer_min: Minutes after the session open before submission is allowed.
        close_buffer_min: Minutes before the session close after which submission
            is blocked (prevents close-buffer DAY-order resubmission churn).

    Returns:
        ``(allowed, reason)`` — ``allowed`` is True only inside
        ``[open + open_buffer_min, close - close_buffer_min]``; ``reason`` always
        explains the decision.
    """
    d = now_et.date()

    if _XNYS is not None:
        try:
            bounds = _session_bounds_mcal(d)
        except Exception:
            log.exception("pandas_market_calendars lookup failed — using fallback")
            bounds = _session_bounds_fallback(d)
    else:
        bounds = _session_bounds_fallback(d)

    if bounds is None:
        return False, f"market closed on {d.isoformat()} (weekend/holiday)"

    open_t, close_t = bounds
    open_dt = _dt.datetime.combine(d, open_t)
    close_dt = _dt.datetime.combine(d, close_t)
    window_start = open_dt + _dt.timedelta(minutes=open_buffer_min)
    window_end = close_dt - _dt.timedelta(minutes=close_buffer_min)

    # Compare on naive wall-clock ET to stay independent of tzinfo presence.
    now_naive = now_et.replace(tzinfo=None)
    now_hm = now_naive.time().isoformat(timespec="minutes")

    if now_naive < open_dt:
        return False, f"pre-open: {now_hm} ET < open {open_t.isoformat(timespec='minutes')}"
    if now_naive >= close_dt:
        return False, f"post-close: {now_hm} ET >= close {close_t.isoformat(timespec='minutes')}"
    if now_naive < window_start:
        return False, (
            f"inside open buffer ({open_buffer_min}m): {now_hm} ET "
            f"< {window_start.time().isoformat(timespec='minutes')}"
        )
    if now_naive >= window_end:
        return False, (
            f"inside close buffer ({close_buffer_min}m): {now_hm} ET "
            f">= {window_end.time().isoformat(timespec='minutes')}"
        )

    return True, (
        f"tradeable: {now_hm} ET inside "
        f"[{window_start.time().isoformat(timespec='minutes')}, "
        f"{window_end.time().isoformat(timespec='minutes')}]"
    )
