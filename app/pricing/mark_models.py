"""Mark a held call with realized / entry_iv / prev_iv (allocation-engine PR #62)."""

from __future__ import annotations

from typing import Dict, List, Optional

from app.pricing.black_scholes import (
    R,
    bs_call_implied_vol,
    bs_call_price,
    realized_vol,
    year_fraction,
)

MODELS = ("realized", "entry_iv", "prev_iv")


def run_pricer_backtest(
    underlying: Dict[str, float],
    option: Dict[str, float],
    strike: float,
    expiry: str,
    r: float = R,
    vol_window: int = 20,
    start: Optional[str] = None,
) -> List[dict]:
    """Price the call on each day with both underlying and option closes."""
    days = sorted(d for d in option if d in underlying and (start is None or d >= start))
    u_days = sorted(underlying)
    rows: List[dict] = []
    entry_iv: Optional[float] = None
    prev_iv: Optional[float] = None

    for d in days:
        s = underlying[d]
        actual = option[d]
        t = year_fraction(d, expiry)
        hist = [underlying[x] for x in u_days if x <= d]
        rv = realized_vol(hist, vol_window)
        iv_today = bs_call_implied_vol(actual, s, strike, t, r)
        if entry_iv is None and iv_today is not None:
            entry_iv = iv_today

        row = {"date": d, "spot": s, "actual": actual, "iv": iv_today, "rv": rv}
        for name, sigma in (("realized", rv), ("entry_iv", entry_iv), ("prev_iv", prev_iv)):
            row[name] = bs_call_price(s, strike, t, sigma, r) if sigma else None
        rows.append(row)
        if iv_today is not None:
            prev_iv = iv_today
    return rows


def _state_through(
    underlying: Dict[str, float],
    option: Dict[str, float],
    strike: float,
    expiry: str,
    through: str,
    *,
    r: float = R,
    vol_window: int = 20,
    start: Optional[str] = None,
) -> dict:
    """IV state after the last historical day on or before `through`."""
    rows = run_pricer_backtest(
        underlying, option, strike, expiry, r, vol_window, start,
    )
    rows = [r for r in rows if r["date"] <= through]
    if not rows:
        raise ValueError(f"no option/underlying history on or before {through}")
    last = rows[-1]
    return {
        "prev_day": last["date"],
        "prev_spot": last["spot"],
        "prev_option_close": last["actual"],
        "prev_iv": last["iv"],
        "entry_iv": _entry_iv(rows),
        "realized_vol": last["rv"],
    }


def _entry_iv(rows: List[dict]) -> Optional[float]:
    for row in rows:
        if row.get("iv") is not None:
            return row["iv"]
    return None


def mark_call_at_spot(
    spot: float,
    *,
    strike: float,
    expiry: str,
    as_of: str,
    underlying: Dict[str, float],
    option: Dict[str, float],
    r: float = R,
    vol_window: int = 20,
    start: Optional[str] = "2026-09-29",
) -> dict:
    """Reprice NBIS-style held calls at an overnight or live spot.

    Uses closes strictly before `as_of` to set prev_iv (yesterday's IV),
    entry_iv (first bar in window), and trailing realized vol; then applies
    those sigmas at `spot` with time to expiry from `as_of`.
    """
    prior_days = sorted(d for d in option if d in underlying and d < as_of)
    if not prior_days:
        raise ValueError(f"need at least one history day before {as_of}")
    prev_day = prior_days[-1]
    state = _state_through(
        underlying, option, strike, expiry, prev_day, r=r, vol_window=vol_window, start=start,
    )
    t = year_fraction(as_of, expiry)
    marks = {}
    for name in MODELS:
        sigma = state["realized_vol"] if name == "realized" else state[name]
        marks[name] = round(bs_call_price(spot, strike, t, sigma, r), 2) if sigma else None

    iv_at_prev = state["prev_iv"]
    return {
        "symbol": underlying.get("_symbol") or option.get("_symbol"),
        "occ": option.get("_occ"),
        "strike": strike,
        "expiry": expiry,
        "as_of": as_of,
        "spot": spot,
        "dte": round(t * 365),
        "prev_day": prev_day,
        "prev_spot": state["prev_spot"],
        "prev_option_close": state["prev_option_close"],
        "prev_iv": iv_at_prev,
        "entry_iv": state["entry_iv"],
        "realized_vol": state["realized_vol"],
        "marks": marks,
        "primary": marks.get("prev_iv"),
    }
