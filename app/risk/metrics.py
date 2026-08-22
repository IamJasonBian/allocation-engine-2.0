"""Performance metrics over the book's equity curve.

Everything is computed from daily P&L and return-on-exposure, restricted to
days with prior exposure (flat days are zero by construction and would dilute
vol and Sharpe). Risk-free rate is taken as zero: the book is a trading
overlay, not a cash-benchmarked fund.
"""

from math import sqrt
from statistics import mean

from app.risk._math import (
    annualize_vol, excess_kurtosis, percentile, periods_per_year, r, sample_std, skewness,
)


def drawdowns(curve: list[dict]) -> dict:
    """Max drawdown of cumulative P&L from its running peak, in USD and as % of peak gross exposure.

    Also returns the drawdown series (for charting) and the longest
    peak-to-recovery stretch in days (still open if it hasn't recovered).
    """
    peak = -float("inf")
    peak_date = None
    max_dd = 0.0
    max_dd_pct = 0.0
    trough_date = None
    dd_start = None
    series: list[dict] = []
    longest_days = 0
    current_start = None

    for p in curve:
        if p["totalPnl"] > peak:
            peak = p["totalPnl"]
            peak_date = p["date"]
            if current_start is not None:
                longest_days = max(longest_days, _days_between(current_start, p["date"]))
            current_start = None
        dd = p["totalPnl"] - peak
        if dd < 0 and current_start is None:
            current_start = peak_date
        pct = dd / p["grossExposure"] if p["grossExposure"] > 1e-9 else 0.0
        series.append({"date": p["date"], "drawdownUsd": r(dd, 2), "drawdownPct": r(pct * 100, 3)})
        if dd < max_dd:
            max_dd = dd
            max_dd_pct = pct
            trough_date = p["date"]
            dd_start = peak_date
    if current_start is not None and curve:
        longest_days = max(longest_days, _days_between(current_start, curve[-1]["date"]))
    return {
        "maxDrawdownUsd": r(max_dd, 2),
        "maxDrawdownPct": r(max_dd_pct * 100, 3),
        "drawdownStart": dd_start,
        "drawdownTrough": trough_date,
        "longestDrawdownDays": longest_days,
        "inDrawdown": bool(series) and series[-1]["drawdownUsd"] < 0,
        "currentDrawdownUsd": series[-1]["drawdownUsd"] if series else 0.0,
        "series": series,
    }


def _days_between(a: str, b: str) -> int:
    from datetime import date
    return (date.fromisoformat(b) - date.fromisoformat(a)).days


def performance(curve: list[dict]) -> dict:
    """Return / vol / Sharpe / Sortino / hit-rate family for the book curve."""
    active = [p for p in curve[1:] if p["returnOnExposure"] != 0.0 or p["dailyPnl"] != 0.0]
    rets = [p["returnOnExposure"] for p in active]
    pnls = [p["dailyPnl"] for p in active]
    dates = [p["date"] for p in curve]
    ppy = periods_per_year(dates)
    out = {
        "observations": len(rets),
        "periodsPerYear": ppy,
        "totalPnlUsd": curve[-1]["totalPnl"] if curve else 0.0,
        "firstDate": dates[0] if dates else None,
        "lastDate": dates[-1] if dates else None,
    }
    if len(rets) < 2:
        return out

    mu = mean(rets)
    sd = sample_std(rets)
    downside = sqrt(sum(min(x, 0) ** 2 for x in rets) / len(rets))
    wins = [x for x in pnls if x > 0]
    losses = [x for x in pnls if x < 0]
    gross_win = sum(wins)
    gross_loss = -sum(losses)
    avg_exposure = mean(p["grossExposure"] for p in curve if p["grossExposure"] > 0) if curve else 0.0

    out.update({
        "avgGrossExposureUsd": r(avg_exposure, 2),
        "returnOnAvgExposurePct": r(out["totalPnlUsd"] / avg_exposure * 100, 3) if avg_exposure else 0.0,
        "meanDailyReturnPct": r(mu * 100, 4),
        "dailyVolPct": r(sd * 100, 4),
        "annualizedReturnPct": r(mu * ppy * 100, 2),
        "annualizedVolPct": r(annualize_vol(sd, ppy) * 100, 2),
        "sharpe": r(mu / sd * sqrt(ppy), 3) if sd > 1e-12 else 0.0,
        "sortino": r(mu / downside * sqrt(ppy), 3) if downside > 1e-12 else 0.0,
        "hitRatePct": r(len(wins) / len(pnls) * 100, 2) if pnls else 0.0,
        "profitFactor": r(gross_win / gross_loss, 3) if gross_loss > 1e-9 else None,
        "avgWinUsd": r(mean(wins), 2) if wins else 0.0,
        "avgLossUsd": r(mean(losses), 2) if losses else 0.0,
        "bestDayUsd": r(max(pnls), 2),
        "worstDayUsd": r(min(pnls), 2),
        "bestDayPct": r(max(rets) * 100, 3),
        "worstDayPct": r(min(rets) * 100, 3),
        "medianDailyReturnPct": r(percentile(rets, 0.5) * 100, 4),
        "skew": r(skewness(rets), 3),
        "excessKurtosis": r(excess_kurtosis(rets), 3),
        "dailyPnlStdUsd": r(sample_std(pnls), 2),
    })
    dd = drawdowns(curve)
    out["maxDrawdownUsd"] = dd["maxDrawdownUsd"]
    out["maxDrawdownPct"] = dd["maxDrawdownPct"]
    ann_ret = mu * ppy
    out["calmar"] = (
        r(ann_ret / abs(dd["maxDrawdownPct"] / 100), 3) if abs(dd["maxDrawdownPct"]) > 1e-9 else None
    )
    return out


def rolling_vol(curve: list[dict], window: int = 20) -> list[dict]:
    """Trailing-window annualized vol of return-on-exposure, for the chart."""
    dates = [p["date"] for p in curve]
    ppy = periods_per_year(dates)
    rets = [p["returnOnExposure"] for p in curve]
    out = []
    for i in range(window, len(rets) + 1):
        chunk = [x for x in rets[i - window:i]]
        out.append({"date": dates[i - 1], "annualizedVolPct": r(annualize_vol(sample_std(chunk), ppy) * 100, 2)})
    return out
