"""Small numeric helpers (stdlib only) shared by the risk modules."""

from datetime import date
from math import sqrt
from statistics import mean, stdev


def sample_std(xs: list[float]) -> float:
    return stdev(xs) if len(xs) >= 2 else 0.0


def percentile(xs: list[float], q: float) -> float:
    """Linear-interpolated percentile, q in [0, 1]. Empty → 0."""
    if not xs:
        return 0.0
    s = sorted(xs)
    if len(s) == 1:
        return s[0]
    pos = q * (len(s) - 1)
    lo = int(pos)
    hi = min(lo + 1, len(s) - 1)
    return s[lo] + (s[hi] - s[lo]) * (pos - lo)


def skewness(xs: list[float]) -> float:
    n = len(xs)
    if n < 3:
        return 0.0
    mu = mean(xs)
    sd = sample_std(xs)
    if sd < 1e-12:
        return 0.0
    m3 = sum((x - mu) ** 3 for x in xs) / n
    return m3 / sd ** 3


def excess_kurtosis(xs: list[float]) -> float:
    n = len(xs)
    if n < 4:
        return 0.0
    mu = mean(xs)
    sd = sample_std(xs)
    if sd < 1e-12:
        return 0.0
    m4 = sum((x - mu) ** 4 for x in xs) / n
    return m4 / sd ** 4 - 3.0


def covariance(xs: list[float], ys: list[float]) -> float:
    n = len(xs)
    if n < 2:
        return 0.0
    mx, my = mean(xs), mean(ys)
    return sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / (n - 1)


def correlation(xs: list[float], ys: list[float]) -> float:
    sx, sy = sample_std(xs), sample_std(ys)
    if sx < 1e-12 or sy < 1e-12:
        return 0.0
    return covariance(xs, ys) / (sx * sy)


def periods_per_year(dates: list[str]) -> int:
    """Annualization calendar implied by sampling density.

    Near-daily closes (crypto, 7 days/week) → 365; trading-day closes → 252.
    """
    if len(dates) < 2:
        return 252
    span = (date.fromisoformat(dates[-1]) - date.fromisoformat(dates[0])).days or 1
    return 365 if len(dates) / span > 0.9 else 252


def annualize_vol(daily_std: float, ppy: int) -> float:
    return daily_std * sqrt(ppy)


def r(x: float, nd: int = 4) -> float:
    return round(x, nd)
