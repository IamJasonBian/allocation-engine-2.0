"""
Metrics computation for backtest results.

Computes return, Sharpe, drawdown, pair statistics, and
buy-and-hold comparisons from simulation output.
"""

import math
from dataclasses import dataclass
from typing import Dict, List, Optional

from trading_system.backtests.simulation import (
    PairState,
    PairedOrder,
    SimulationResult,
    run_simulation,
)


@dataclass
class BacktestMetrics:
    symbol: str
    total_return_pct: float
    annualized_return_pct: float
    sharpe_ratio: float
    max_drawdown_pct: float
    max_drawdown_start: str
    max_drawdown_end: str
    final_net_value: float
    initial_value: float
    # Pair stats
    pairs_placed: int
    pairs_completed: int
    pairs_sell_only: int
    pairs_cancelled: int
    completion_rate_pct: float
    avg_days_to_complete: float
    avg_cost_basis_improvement_pct: float
    # Buy-and-hold comparison
    buy_hold_return_pct: float
    buy_hold_final_value: float
    excess_return_pct: float


def compute_metrics(result: SimulationResult, risk_free_rate: float = 0.05) -> BacktestMetrics:
    """Compute comprehensive metrics from a simulation result."""
    snapshots = result.snapshots
    if len(snapshots) < 2:
        raise ValueError("Need at least 2 snapshots to compute metrics")

    initial_value = result.initial_shares * result.initial_price
    final_value = snapshots[-1].net_value

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
        prev_val = snapshots[i - 1].net_value
        if prev_val > 0:
            daily_returns.append((snapshots[i].net_value - prev_val) / prev_val)

    sharpe = _sharpe_ratio(daily_returns, risk_free_rate)

    # Max drawdown
    dd_pct, dd_start, dd_end = _max_drawdown(snapshots)

    # Pair statistics
    pairs = result.pairs
    completed = [p for p in pairs if p.state == PairState.COMPLETED]
    placed = result.portfolio.pairs_placed
    completion_rate = (len(completed) / placed * 100) if placed > 0 else 0.0

    avg_days = _avg_days_to_complete(completed, result.snapshots)
    avg_cbi = _avg_cost_basis_improvement(completed)

    # Buy-and-hold
    bh_final = result.initial_shares * snapshots[-1].price
    bh_return = (bh_final - initial_value) / initial_value * 100

    return BacktestMetrics(
        symbol=result.symbol,
        total_return_pct=round(total_return, 2),
        annualized_return_pct=round(annualized, 2),
        sharpe_ratio=round(sharpe, 3),
        max_drawdown_pct=round(dd_pct, 2),
        max_drawdown_start=dd_start,
        max_drawdown_end=dd_end,
        final_net_value=round(final_value, 2),
        initial_value=round(initial_value, 2),
        pairs_placed=placed,
        pairs_completed=result.portfolio.pairs_completed,
        pairs_sell_only=result.portfolio.pairs_sell_only,
        pairs_cancelled=result.portfolio.pairs_cancelled,
        completion_rate_pct=round(completion_rate, 2),
        avg_days_to_complete=round(avg_days, 1),
        avg_cost_basis_improvement_pct=round(avg_cbi, 4),
        buy_hold_return_pct=round(bh_return, 2),
        buy_hold_final_value=round(bh_final, 2),
        excess_return_pct=round(total_return - bh_return, 2),
    )


def buy_hold_comparison(result: SimulationResult) -> List[Dict]:
    """Return daily buy-and-hold values alongside DCA values for charting."""
    initial_value = result.initial_shares * result.initial_price
    rows = []
    for snap in result.snapshots:
        bh_value = result.initial_shares * snap.price
        rows.append({
            "date": snap.date,
            "dca_value": snap.net_value,
            "buy_hold_value": bh_value,
            "dca_return_pct": (snap.net_value - initial_value) / initial_value * 100,
            "buy_hold_return_pct": (bh_value - initial_value) / initial_value * 100,
        })
    return rows


def parameter_sensitivity(
    bars: List[Dict],
    symbol: str,
    initial_shares: int,
    initial_price: float,
    param_name: str,
    param_values: List,
    base_params: Optional[Dict] = None,
    risk_free_rate: float = 0.05,
) -> List[Dict]:
    """
    Re-run simulation varying one parameter, return metrics per value.

    Args:
        param_name: One of 'stop_offset_pct', 'buy_offset', 'coverage_threshold'
        param_values: List of values to test
        base_params: Dict of base simulation parameters (defaults used if None)
    """
    defaults = {
        "stop_offset_pct": 0.01,
        "buy_offset": 0.20,
        "coverage_threshold": 0.20,
        "proximity_pct": 0.0075,
        "lot_size": 100,
    }
    if base_params:
        defaults.update(base_params)

    results = []
    for val in param_values:
        params = dict(defaults)
        params[param_name] = val

        sim = run_simulation(
            bars=bars,
            symbol=symbol,
            initial_shares=initial_shares,
            initial_price=initial_price,
            **params,
        )
        m = compute_metrics(sim, risk_free_rate)
        results.append({
            "param_value": val,
            "total_return_pct": m.total_return_pct,
            "annualized_return_pct": m.annualized_return_pct,
            "sharpe_ratio": m.sharpe_ratio,
            "max_drawdown_pct": m.max_drawdown_pct,
            "completion_rate_pct": m.completion_rate_pct,
            "pairs_placed": m.pairs_placed,
            "excess_return_pct": m.excess_return_pct,
        })
    return results


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
    peak_val = snapshots[0].net_value
    peak_date = snapshots[0].date
    max_dd = 0.0
    dd_start = snapshots[0].date
    dd_end = snapshots[0].date

    for snap in snapshots:
        if snap.net_value > peak_val:
            peak_val = snap.net_value
            peak_date = snap.date
        dd = (peak_val - snap.net_value) / peak_val * 100 if peak_val > 0 else 0.0
        if dd > max_dd:
            max_dd = dd
            dd_start = peak_date
            dd_end = snap.date

    return max_dd, dd_start, dd_end


def _avg_days_to_complete(completed_pairs: List[PairedOrder], snapshots) -> float:
    """Average trading days between pair placement and buy fill."""
    if not completed_pairs:
        return 0.0
    date_to_idx = {s.date: i for i, s in enumerate(snapshots)}
    total_days = 0
    counted = 0
    for p in completed_pairs:
        placed_idx = date_to_idx.get(p.placed_date)
        fill_idx = date_to_idx.get(p.buy_fill_date)
        if placed_idx is not None and fill_idx is not None:
            total_days += fill_idx - placed_idx
            counted += 1
    return total_days / counted if counted > 0 else 0.0


def _avg_cost_basis_improvement(completed_pairs: List[PairedOrder]) -> float:
    """Average cost basis improvement per completed pair as a percentage."""
    if not completed_pairs:
        return 0.0
    improvements = []
    for p in completed_pairs:
        if p.sell_fill_price and p.buy_fill_price and p.sell_fill_price > 0:
            improvement = (p.sell_fill_price - p.buy_fill_price) / p.sell_fill_price * 100
            improvements.append(improvement)
    return sum(improvements) / len(improvements) if improvements else 0.0
