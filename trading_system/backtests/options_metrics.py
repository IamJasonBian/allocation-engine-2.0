"""
Metrics computation for options-based hedging simulations.

Mirrors the interface of metrics.py but operates on OptionsSimulationResult.
"""

import math
from dataclasses import dataclass
from typing import List

from trading_system.backtests.options_simulation import OptionsSimulationResult


@dataclass
class OptionsBacktestMetrics:
    symbol: str
    strategy_type: str          # e.g. "PROTECTIVE_PUT", "COLLAR"
    total_return_pct: float
    annualized_return_pct: float
    sharpe_ratio: float
    max_drawdown_pct: float
    max_drawdown_start: str
    max_drawdown_end: str
    final_net_value: float
    initial_value: float
    # Options cost stats
    total_premium_paid: float
    total_premium_received: float
    net_premium_cost: float     # paid - received
    total_intrinsic_recovered: float   # put intrinsic at settlements
    total_call_intrinsic_paid: float   # call intrinsic at settlements
    protection_cost_pct: float  # net cost / initial value * 100
    num_rolls: int


def compute_options_metrics(
    result: OptionsSimulationResult,
    risk_free_rate: float = 0.05,
) -> OptionsBacktestMetrics:
    """Compute metrics from an options simulation result."""
    snapshots = result.snapshots
    if len(snapshots) < 2:
        raise ValueError("Need at least 2 snapshots to compute metrics")

    initial_value = result.initial_shares * result.initial_price
    final_value = snapshots[-1].net_portfolio_value

    # Total return
    total_return = (final_value - initial_value) / initial_value * 100

    # Annualized return
    trading_days = len(snapshots)
    years = trading_days / 252
    if years > 0 and final_value > 0 and initial_value > 0:
        annualized = ((final_value / initial_value) ** (1 / years) - 1) * 100
    else:
        annualized = 0.0

    # Daily returns for Sharpe
    daily_returns = []
    for i in range(1, len(snapshots)):
        prev_val = snapshots[i - 1].net_portfolio_value
        if prev_val > 0:
            daily_returns.append(
                (snapshots[i].net_portfolio_value - prev_val) / prev_val
            )

    sharpe = _sharpe_ratio(daily_returns, risk_free_rate)

    # Max drawdown
    dd_pct, dd_start, dd_end = _max_drawdown(snapshots)

    # Options cost stats from final snapshot
    final = snapshots[-1]
    total_paid = final.cumulative_premium_paid
    total_received = final.cumulative_premium_received
    net_cost = total_paid - total_received
    total_put_intrinsic = final.cumulative_put_intrinsic
    total_call_intrinsic = final.cumulative_call_intrinsic
    protection_cost = (net_cost - total_put_intrinsic + total_call_intrinsic) / initial_value * 100

    return OptionsBacktestMetrics(
        symbol=result.symbol,
        strategy_type=result.strategy_type.value,
        total_return_pct=round(total_return, 2),
        annualized_return_pct=round(annualized, 2),
        sharpe_ratio=round(sharpe, 3),
        max_drawdown_pct=round(dd_pct, 2),
        max_drawdown_start=dd_start,
        max_drawdown_end=dd_end,
        final_net_value=round(final_value, 2),
        initial_value=round(initial_value, 2),
        total_premium_paid=round(total_paid, 2),
        total_premium_received=round(total_received, 2),
        net_premium_cost=round(net_cost, 2),
        total_intrinsic_recovered=round(total_put_intrinsic, 2),
        total_call_intrinsic_paid=round(total_call_intrinsic, 2),
        protection_cost_pct=round(protection_cost, 2),
        num_rolls=len(result.rolls),
    )


# ── Private helpers ──────────────────────────────────────────────────────


def _sharpe_ratio(daily_returns: List[float], risk_free_rate: float) -> float:
    """Annualized Sharpe ratio from daily returns."""
    if len(daily_returns) < 2:
        return 0.0
    mean_r = sum(daily_returns) / len(daily_returns)
    var = sum((r - mean_r) ** 2 for r in daily_returns) / (len(daily_returns) - 1)
    std = math.sqrt(var) if var > 0 else 0.0
    if std == 0:
        return 0.0
    daily_rf = risk_free_rate / 252
    return (mean_r - daily_rf) / std * math.sqrt(252)


def _max_drawdown(snapshots) -> tuple:
    """Return (max_drawdown_pct, peak_date, trough_date)."""
    peak_val = snapshots[0].net_portfolio_value
    peak_date = snapshots[0].date
    max_dd = 0.0
    dd_start = snapshots[0].date
    dd_end = snapshots[0].date

    for snap in snapshots:
        if snap.net_portfolio_value > peak_val:
            peak_val = snap.net_portfolio_value
            peak_date = snap.date
        dd = ((peak_val - snap.net_portfolio_value) / peak_val * 100
              if peak_val > 0 else 0.0)
        if dd > max_dd:
            max_dd = dd
            dd_start = peak_date
            dd_end = snap.date

    return max_dd, dd_start, dd_end
