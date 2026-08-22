"""How the pieces move together: Σ, ρ, true portfolio σ, and who owns it.

Weights are signed USD notionals (position × last close), so a short carries
a negative weight and a hedge reduces σ. Portfolio daily σ = sqrt(wᵀΣw).
Marginal contribution to risk (MCTR_i = (Σw)_i / σ) and component risk
(CR_i = w_i × MCTR_i) decompose σ exactly: Σ CR_i = σ. A position with
negative component risk is a hedge.
"""

from math import sqrt

from app.risk._math import annualize_vol, correlation, covariance, periods_per_year, r, sample_std


def covariance_risk(dates: list[str], returns: dict[str, list[float]], book: dict[str, dict]) -> dict:
    """Covariance-based portfolio volatility and its decomposition.

    Args:
        dates: Aligned close dates (len(returns[s]) == len(dates) - 1).
        returns: {SYMBOL: daily growth rates} on the same dates.
        book: {SYMBOL: {notional, ...}} — current signed USD exposure.

    Returns:
        Dict with `portfolioDailySigmaUsd`, bounds (`additiveSigmaUsd` ≥ σ ≥
        `uncorrelatedSigmaUsd` for a long-only book), `diversificationRatio`,
        per-symbol `contributions`, and the `correlation` matrix.
    """
    syms = sorted(s for s in returns if s in book and abs(book[s]["notional"]) > 1e-9 and len(returns[s]) >= 2)
    if not syms:
        return {"symbols": [], "observations": 0}

    w = {s: book[s]["notional"] for s in syms}
    sigma = {s: sample_std(returns[s]) for s in syms}
    cov = {a: {b: covariance(returns[a], returns[b]) for b in syms} for a in syms}
    corr = {a: {b: correlation(returns[a], returns[b]) for b in syms} for a in syms}

    sigma_w = {a: sum(cov[a][b] * w[b] for b in syms) for a in syms}  # (Σw)_i
    var = sum(w[a] * sigma_w[a] for a in syms)
    port_sigma = sqrt(max(var, 0.0))

    standalone = {s: abs(w[s]) * sigma[s] for s in syms}
    additive = sum(standalone.values())
    uncorrelated = sqrt(sum(x * x for x in standalone.values()))

    contributions = []
    for s in syms:
        mctr = sigma_w[s] / port_sigma if port_sigma > 1e-12 else 0.0
        cr = w[s] * mctr
        contributions.append({
            "symbol": s,
            "notional": r(w[s], 2),
            "dailySigmaPct": r(sigma[s] * 100, 4),
            "standaloneSigmaUsd": r(standalone[s], 2),
            "marginalContribution": r(mctr, 6),
            "componentRiskUsd": r(cr, 2),
            "componentRiskPct": r(cr / port_sigma * 100, 2) if port_sigma > 1e-12 else 0.0,
            "isHedge": cr < 0,
        })

    ppy = periods_per_year(dates)
    return {
        "symbols": syms,
        "observations": len(dates) - 1,
        "periodsPerYear": ppy,
        "portfolioDailySigmaUsd": r(port_sigma, 2),
        "portfolioAnnualSigmaUsd": r(annualize_vol(port_sigma, ppy), 2),
        "additiveSigmaUsd": r(additive, 2),
        "uncorrelatedSigmaUsd": r(uncorrelated, 2),
        "diversificationRatio": r(additive / port_sigma, 3) if port_sigma > 1e-12 else None,
        "contributions": contributions,
        "correlation": {a: {b: r(corr[a][b], 3) for b in syms} for a in syms},
    }
