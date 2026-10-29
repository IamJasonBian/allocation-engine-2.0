"""Pure-Python Black–Scholes (no scipy). Shared by executor and mark models."""

from __future__ import annotations

import math
from datetime import date

R = 0.04


def _ndtr(x: float) -> float:
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def year_fraction(day: str, expiry: str) -> float:
    """Calendar time to expiry in years (close-to-close)."""
    d0 = date.fromisoformat(day)
    d1 = date.fromisoformat(expiry)
    return max((d1 - d0).days, 0) / 365.0


def bs_call_price(s: float, k: float, t: float, sigma: float, r: float = R) -> float:
    if t <= 1e-8:
        return max(s - k, 0.0)
    if s <= 0 or k <= 0 or sigma <= 0:
        return max(s - k, 0.0)
    vs = sigma * math.sqrt(t)
    d1 = (math.log(s / k) + (r + 0.5 * sigma * sigma) * t) / vs
    d2 = d1 - vs
    return s * _ndtr(d1) - k * math.exp(-r * t) * _ndtr(d2)


def bs_call_delta(s: float, k: float, t: float, sigma: float, r: float = R) -> float:
    if t <= 1e-8:
        return 1.0 if s > k else 0.0
    vs = sigma * math.sqrt(t)
    d1 = (math.log(s / k) + (r + 0.5 * sigma * sigma) * t) / vs
    return _ndtr(d1)


def bs_call_implied_vol(
    price: float,
    s: float,
    k: float,
    t: float,
    r: float = R,
    lo: float = 0.01,
    hi: float = 4.0,
) -> float | None:
    if t <= 1e-8 or price <= 0:
        return None
    intrinsic = max(s - k * math.exp(-r * t), 0.0)
    if price <= intrinsic or price >= s:
        return None
    for _ in range(70):
        mid = 0.5 * (lo + hi)
        if bs_call_price(s, k, t, mid, r) > price:
            hi = mid
        else:
            lo = mid
    return 0.5 * (lo + hi)


def realized_vol(closes: list[float], window: int = 20) -> float | None:
    """Annualized close-to-close vol over the last `window` returns."""
    if len(closes) < window + 1:
        return None
    tail = closes[-(window + 1) :]
    rets = [math.log(b / a) for a, b in zip(tail, tail[1:])]
    mean = sum(rets) / len(rets)
    var = sum((x - mean) ** 2 for x in rets) / (len(rets) - 1)
    return math.sqrt(var * 252)


def occ_symbol(symbol: str, expiry: str, strike: float, right: str = "C") -> str:
    return f"{symbol}{date.fromisoformat(expiry):%y%m%d}{right}{int(round(strike * 1000)):08d}"
