"""Assemble the full metrics & risk report, with plain-English flags.

`build_report` is the one entry point the API and the dashboard use. Each
section is independently computable; the flags at the top are the
operator-facing summary — each names the number it came from so it can be
checked against the section below it.
"""

from datetime import datetime, timezone

from app.pnl import compute_pnl, pnl_risk
from app.risk._math import r
from app.risk.covariance import covariance_risk
from app.risk.curve import aligned_returns, current_book, per_symbol_marks, portfolio_curve
from app.risk.exposure import exposure
from app.risk.metrics import drawdowns, performance, rolling_vol
from app.risk.stress import stress_scenarios
from app.risk.tail import historical_var, parametric_var


def per_symbol(fills: list[dict], closes_by_symbol: dict[str, list[dict]]) -> list[dict]:
    rows = []
    for sym in sorted(closes_by_symbol):
        risk = pnl_risk(fills, closes_by_symbol[sym], sym)
        series = risk.pop("series", [])
        if not series:
            continue
        closes = [p["close"] for p in series]
        rows.append({
            "symbol": sym.upper(),
            "asOf": series[-1]["date"],
            "lastClose": series[-1]["close"],
            "position": risk["position"],
            "notional": r(risk["position"] * series[-1]["close"], 2),
            "totalPnl": series[-1]["totalPnl"],
            "observations": risk["observations"],
            "closeStdUsd": risk.get("closeStdUsd"),
            "growthRateMeanPct": risk.get("growthRateMeanPct"),
            "growthRateStdPct": risk.get("growthRateStdPct"),
            "downsideGrowthRateStdPct": risk.get("downsideGrowthRateStdPct"),
            "growthRateZ": risk.get("growthRateZ"),
            "riskUsd": risk.get("riskUsd"),
            "return30dPct": r((closes[-1] / closes[-31] - 1) * 100, 2) if len(closes) > 31 else None,
            "sparkline": closes[-60:],
            "zSeries": (risk.get("growthRateZSeries") or [])[-60:],
        })
    return rows


def flags(perf: dict, expo: dict, cov: dict, tail: dict, symbols: list[dict], dd: dict, stress: dict) -> list[dict]:
    """Operator flags. severity: 'critical' | 'warning' | 'info'."""
    out = []
    gross = expo["grossExposureUsd"]

    if expo["openPositions"] and expo["largestPositionPct"] >= 50:
        out.append({"severity": "warning", "code": "concentration",
                    "text": f"{expo['largestPosition']} is {expo['largestPositionPct']}% of gross exposure "
                            f"(effective N = {expo['effectiveN']})."})
    if dd["inDrawdown"] and gross and abs(dd["currentDrawdownUsd"]) / gross >= 0.05:
        out.append({"severity": "warning", "code": "drawdown",
                    "text": f"Book is ${abs(dd['currentDrawdownUsd']):,.0f} below its P&L peak "
                            f"({abs(dd['currentDrawdownUsd']) / gross * 100:.1f}% of gross)."})
    lvl99 = tail.get("levels", {}).get("99")
    if lvl99 and gross and lvl99["varUsd"] / gross >= 0.08:
        out.append({"severity": "critical", "code": "tail",
                    "text": f"1-day 99% VaR is ${lvl99['varUsd']:,.0f} — {lvl99['varUsd'] / gross * 100:.1f}% of gross. "
                            f"Expected loss beyond it (CVaR): ${lvl99['cvarUsd']:,.0f}."})
    for s in symbols:
        z = s.get("growthRateZ")
        if z is not None and abs(z) >= 2 and abs(s["notional"]) > 1e-9:
            out.append({"severity": "info", "code": "zscore",
                        "text": f"{s['symbol']} printed a {z:+.1f}σ day on {s['asOf']}."})
    if perf.get("observations", 0) >= 20 and perf.get("skew", 0) <= -0.5:
        out.append({"severity": "info", "code": "skew",
                    "text": f"Daily returns are left-skewed (skew {perf['skew']}, excess kurtosis "
                            f"{perf['excessKurtosis']}): losses cluster larger than gains."})
    corr = cov.get("correlation", {})
    pairs = [(a, b, corr[a][b]) for a in corr for b in corr if a < b and corr[a][b] >= 0.8]
    if pairs:
        a, b, c = max(pairs, key=lambda x: x[2])
        out.append({"severity": "info", "code": "correlation",
                    "text": f"{a} and {b} move together (ρ = {c:.2f}); they are closer to one position than two."})
    hedges = [c["symbol"] for c in cov.get("contributions", []) if c["isHedge"]]
    if hedges:
        out.append({"severity": "info", "code": "hedge",
                    "text": f"{', '.join(hedges)} reduce portfolio σ (negative component risk)."})
    dr = cov.get("diversificationRatio")
    if dr is not None and expo["openPositions"] >= 3 and dr < 1.15:
        out.append({"severity": "warning", "code": "diversification",
                    "text": f"Diversification ratio {dr}: {expo['openPositions']} positions behave almost like one."})
    worst = min((sc for sc in stress.get("scenarios", []) if sc["kind"] == "hypothetical"),
                key=lambda sc: sc["pnlUsd"], default=None)
    if worst and worst["pnlPctOfGross"] <= -15:
        out.append({"severity": "warning", "code": "stress",
                    "text": f"Scenario '{worst['name']}' costs ${-worst['pnlUsd']:,.0f} "
                            f"({-worst['pnlPctOfGross']:.1f}% of gross)."})
    if not out:
        out.append({"severity": "info", "code": "clear", "text": "No risk flags on the current book."})
    return out


def build_report(fills: list[dict], closes_by_symbol: dict[str, list[dict]], *, now: datetime | None = None) -> dict:
    """Full metrics & risk report for the book described by `fills` and `closes_by_symbol`."""
    now = now or datetime.now(timezone.utc)
    marks = per_symbol_marks(fills, closes_by_symbol)
    curve = portfolio_curve(marks)
    book = current_book(marks)
    dates, rets = aligned_returns(marks)

    perf = performance(curve)
    dd = drawdowns(curve)
    expo = exposure(book)
    cov = covariance_risk(dates, rets, book)
    hist = historical_var(rets, book)
    param = parametric_var(cov.get("portfolioDailySigmaUsd", 0.0))
    stress = stress_scenarios(rets, dates, book)
    symbols = per_symbol(fills, closes_by_symbol)
    pnl = compute_pnl(fills, days=None, now=now, mark_prices={s: b["lastClose"] for s, b in book.items()})

    dd_series = dd.pop("series")
    return {
        "generatedAt": now.isoformat(),
        "asOf": curve[-1]["date"] if curve else None,
        "headline": {
            "totalPnlUsd": perf.get("totalPnlUsd", 0.0),
            "realizedPnlUsd": pnl["totalRealizedPnL"],
            "unrealizedPnlUsd": pnl.get("totalUnrealizedPnL", 0.0),
            "grossExposureUsd": expo["grossExposureUsd"],
            "netExposureUsd": expo["netExposureUsd"],
            "portfolioDailySigmaUsd": cov.get("portfolioDailySigmaUsd", 0.0),
            "var95Usd": hist.get("levels", {}).get("95", {}).get("varUsd", 0.0),
            "var99Usd": hist.get("levels", {}).get("99", {}).get("varUsd", 0.0),
            "sharpe": perf.get("sharpe"),
            "maxDrawdownUsd": dd["maxDrawdownUsd"],
            "openPositions": expo["openPositions"],
        },
        "flags": flags(perf, expo, cov, hist, symbols, dd, stress),
        "performance": perf,
        "drawdown": dd,
        "exposure": expo,
        "covariance": cov,
        "tail": {"historical": hist, "parametric": param},
        "stress": stress,
        "symbols": symbols,
        "curve": curve,
        "drawdownSeries": dd_series,
        "rollingVol": rolling_vol(curve),
    }
