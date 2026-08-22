"""How bad is a bad day: VaR and CVaR on the *current* book.

Historical simulation: apply every observed day's joint return vector to
today's notionals — P&L_t = Σ_i w_i · r_{i,t}. This keeps cross-asset
co-movement (a crypto-wide down day hits BTC, ETH and SOL together) without
assuming a distribution. Parametric VaR is the normal-model counterpart from
the covariance σ, shown beside it so fat tails are visible as the gap.
"""

from statistics import NormalDist

from app.risk._math import percentile, r

LEVELS = (0.95, 0.99)


def historical_var(returns: dict[str, list[float]], book: dict[str, dict]) -> dict:
    syms = [s for s in returns if s in book and abs(book[s]["notional"]) > 1e-9]
    if not syms:
        return {"observations": 0, "levels": {}}
    n = min(len(returns[s]) for s in syms)
    pnl = [sum(book[s]["notional"] * returns[s][t] for s in syms) for t in range(n)]
    levels = {}
    for lvl in LEVELS:
        cutoff = percentile(pnl, 1 - lvl)
        tail = [x for x in pnl if x <= cutoff]
        levels[f"{int(lvl * 100)}"] = {
            "varUsd": r(-cutoff, 2),
            "cvarUsd": r(-sum(tail) / len(tail), 2) if tail else r(-cutoff, 2),
            "tailDays": len(tail),
        }
    worst = sorted(range(n), key=lambda t: pnl[t])[:5]
    return {
        "method": "historical_simulation_current_book",
        "observations": n,
        "levels": levels,
        "simulatedPnl": [r(x, 2) for x in pnl],
        "worstDayIndexes": worst,
        "worstDayUsd": r(min(pnl), 2),
        "bestDayUsd": r(max(pnl), 2),
    }


def parametric_var(daily_sigma_usd: float) -> dict:
    z = NormalDist()
    return {
        "method": "normal",
        "levels": {
            f"{int(lvl * 100)}": {
                "varUsd": r(z.inv_cdf(lvl) * daily_sigma_usd, 2),
                # E[X | X > VaR] for a normal: σ·φ(z)/(1-α)
                "cvarUsd": r(daily_sigma_usd * z.pdf(z.inv_cdf(lvl)) / (1 - lvl), 2),
            }
            for lvl in LEVELS
        },
    }
