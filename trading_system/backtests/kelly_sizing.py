"""
Kelly Criterion position sizing backtest.

Computes the optimal bet fraction from historical pair win/loss statistics,
then re-runs the simulation with Kelly-scaled lot sizes to compare against
fixed sizing.

Usage:
    python -m trading_system.backtests.kelly_sizing
    python -m trading_system.backtests.kelly_sizing --symbols BTC SPY --chart
"""

import argparse
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from dotenv import load_dotenv

from trading_system.backtests.data_loader import DATA_DIR, load_daily_data
from trading_system.backtests.metrics import BacktestMetrics, compute_metrics
from trading_system.backtests.pairwise_dca_backtest import (
    DEFAULT_POSITIONS,
    parse_positions,
)
from trading_system.backtests.simulation import (
    PairState,
    SimulationResult,
    run_simulation,
)
from trading_system.config import DEFAULT_LOT_SIZE


@dataclass
class PairOutcomes:
    win_count: int
    loss_count: int
    avg_win: float  # avg gain per share on COMPLETED pairs
    avg_loss: float  # avg loss per share on SELL_ONLY pairs


@dataclass
class KellyResult:
    fraction: float  # f* clamped to [0, 1]
    half_fraction: float
    b_ratio: float  # avg_win / avg_loss
    edge: float  # b*p - q
    win_rate: float  # p


@dataclass
class SizingRun:
    label: str
    lot_size: int
    metrics: BacktestMetrics
    result: SimulationResult


@dataclass
class KellyComparison:
    symbol: str
    outcomes: PairOutcomes
    kelly: KellyResult
    runs: List[SizingRun]  # [fixed, full_kelly, half_kelly]


def extract_pair_outcomes(result: SimulationResult) -> PairOutcomes:
    """Extract win/loss counts and average amounts from simulation pairs."""
    wins = []
    losses = []

    for pair in result.pairs:
        if pair.state == PairState.COMPLETED:
            gain = pair.sell_fill_price - pair.buy_fill_price
            wins.append(gain)
        elif pair.state == PairState.SELL_ONLY:
            # Risk: the spread we tried to capture but lost
            loss = abs(pair.sell_fill_price - pair.buy_limit_price)
            losses.append(loss)

    return PairOutcomes(
        win_count=len(wins),
        loss_count=len(losses),
        avg_win=sum(wins) / len(wins) if wins else 0.0,
        avg_loss=sum(losses) / len(losses) if losses else 0.0,
    )


def compute_kelly_fraction(outcomes: PairOutcomes) -> KellyResult:
    """Compute Kelly optimal fraction from pair outcomes.

    f* = (b*p - q) / b  where:
        p = win probability
        q = 1 - p
        b = avg_win / avg_loss
    """
    total = outcomes.win_count + outcomes.loss_count
    if total == 0 or outcomes.avg_loss == 0:
        return KellyResult(
            fraction=0.0, half_fraction=0.0, b_ratio=0.0, edge=0.0, win_rate=0.0
        )

    p = outcomes.win_count / total
    q = 1.0 - p
    b = outcomes.avg_win / outcomes.avg_loss

    edge = b * p - q
    if b > 0:
        f_star = edge / b
    else:
        f_star = 0.0

    f_star = max(0.0, min(1.0, f_star))

    return KellyResult(
        fraction=f_star,
        half_fraction=f_star / 2.0,
        b_ratio=round(b, 4),
        edge=round(edge, 4),
        win_rate=round(p, 4),
    )


def kelly_lot_size(fraction: float, position_shares: int) -> int:
    """Convert Kelly fraction to a clamped lot size."""
    if fraction <= 0 or position_shares <= 0:
        return 1
    size = int(fraction * position_shares)
    return max(1, min(size, position_shares))


def run_kelly_comparison(
    bars: List[Dict],
    symbol: str,
    initial_shares: int,
    initial_price: float,
) -> KellyComparison:
    """Run fixed vs full-Kelly vs half-Kelly simulations and compare."""
    # Step 1: baseline run with fixed lot size to extract pair statistics
    baseline = run_simulation(
        bars=bars,
        symbol=symbol,
        initial_shares=initial_shares,
        initial_price=initial_price,
        lot_size=DEFAULT_LOT_SIZE,
    )
    outcomes = extract_pair_outcomes(baseline)
    kelly = compute_kelly_fraction(outcomes)

    # Step 2: compute Kelly lot sizes
    full_kelly_lot = kelly_lot_size(kelly.fraction, initial_shares)
    half_kelly_lot = kelly_lot_size(kelly.half_fraction, initial_shares)

    # Step 3: run all three variants
    runs = []
    for label, lot in [
        (f"Fixed({DEFAULT_LOT_SIZE})", DEFAULT_LOT_SIZE),
        ("Full Kelly", full_kelly_lot),
        ("Half Kelly", half_kelly_lot),
    ]:
        result = run_simulation(
            bars=bars,
            symbol=symbol,
            initial_shares=initial_shares,
            initial_price=initial_price,
            lot_size=lot,
        )
        metrics = compute_metrics(result)
        runs.append(SizingRun(label=label, lot_size=lot, metrics=metrics, result=result))

    return KellyComparison(
        symbol=symbol,
        outcomes=outcomes,
        kelly=kelly,
        runs=runs,
    )


def print_kelly_summary(comparison: KellyComparison) -> None:
    """Print formatted terminal table for a Kelly comparison."""
    sym = comparison.symbol
    o = comparison.outcomes
    k = comparison.kelly
    runs = comparison.runs

    print("=" * 64)
    print(f"KELLY CRITERION SIZING — {sym}")
    print("=" * 64)

    total_pairs = o.win_count + o.loss_count
    print("Pair Statistics (baseline):")
    print(
        f"  Win rate:    {k.win_rate * 100:.1f}% ({o.win_count}/{total_pairs})"
        f"   Avg win: ${o.avg_win:.2f}/sh   Avg loss: ${o.avg_loss:.2f}/sh"
    )
    print(
        f"  b ratio:     {k.b_ratio:.2f}"
        f"            Kelly f*: {k.fraction:.3f}"
        f"      Half Kelly: {k.half_fraction:.3f}"
    )
    print()

    # Column headers
    labels = [r.label for r in runs]
    header = f"{'':>18s}" + "".join(f"{l:>14s}" for l in labels)
    print(header)

    # Rows
    def row(name, fmt, attr):
        vals = []
        for r in runs:
            v = getattr(r.metrics, attr)
            vals.append(fmt.format(v))
        print(f"  {name:<16s}" + "".join(f"{v:>14s}" for v in vals))

    row("Total Return", "{:+.2f}%", "total_return_pct")
    row("Sharpe", "{:.3f}", "sharpe_ratio")
    row("Max Drawdown", "{:.2f}%", "max_drawdown_pct")
    row("Pairs Placed", "{:d}", "pairs_placed")
    row("Excess vs B&H", "{:+.2f}%", "excess_return_pct")

    # Lot sizes
    lot_vals = [str(r.lot_size) for r in runs]
    print(f"  {'Lot Size':<16s}" + "".join(f"{v:>14s}" for v in lot_vals))

    print("=" * 64)


def plot_kelly_comparison(
    comparison: KellyComparison,
    bars: List[Dict],
    initial_shares: int,
    initial_price: float,
    output_dir: Optional[Path] = None,
) -> List[Path]:
    """Generate Kelly comparison charts. Returns list of saved file paths."""
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    if output_dir is None:
        output_dir = DATA_DIR
    output_dir.mkdir(parents=True, exist_ok=True)

    sym = comparison.symbol
    saved = []

    # ── Chart 1: Equity curves ────────────────────────────────────────────
    fig, ax = plt.subplots(figsize=(12, 6))

    colors = ["#2196F3", "#FF5722", "#4CAF50"]
    for run, color in zip(comparison.runs, colors):
        dates = [s.date for s in run.result.snapshots]
        values = [s.net_value for s in run.result.snapshots]
        ax.plot(dates, values, label=f"{run.label} (lot={run.lot_size})", color=color, linewidth=1.5)

    # Buy-and-hold dashed line
    bh_values = [initial_shares * s.price for s in comparison.runs[0].result.snapshots]
    dates = [s.date for s in comparison.runs[0].result.snapshots]
    ax.plot(dates, bh_values, label="Buy & Hold", color="#999999", linestyle="--", linewidth=1)

    # Thin out x-axis ticks
    tick_step = max(1, len(dates) // 10)
    ax.set_xticks(range(0, len(dates), tick_step))
    ax.set_xticklabels([dates[i] for i in range(0, len(dates), tick_step)], rotation=45, ha="right")

    ax.set_title(f"{sym} — Kelly Criterion Equity Curves")
    ax.set_ylabel("Portfolio Value ($)")
    ax.legend()
    ax.grid(True, alpha=0.3)
    fig.tight_layout()

    equity_path = output_dir / f"{sym}_kelly_equity.png"
    fig.savefig(equity_path, dpi=150)
    plt.close(fig)
    saved.append(equity_path)
    print(f"  Saved: {equity_path}")

    # ── Chart 2: Kelly sensitivity sweep ──────────────────────────────────
    fractions = [i * 0.05 for i in range(1, 21)]  # 0.05 → 1.0
    returns = []
    sharpes = []

    for frac in fractions:
        lot = kelly_lot_size(frac, initial_shares)
        result = run_simulation(
            bars=bars,
            symbol=sym,
            initial_shares=initial_shares,
            initial_price=initial_price,
            lot_size=lot,
        )
        m = compute_metrics(result)
        returns.append(m.total_return_pct)
        sharpes.append(m.sharpe_ratio)

    fig, ax1 = plt.subplots(figsize=(10, 6))
    ax2 = ax1.twinx()

    ax1.plot(fractions, returns, color="#2196F3", linewidth=2, label="Total Return (%)")
    ax2.plot(fractions, sharpes, color="#FF5722", linewidth=2, label="Sharpe Ratio")

    # Vertical lines at f* and f*/2
    k = comparison.kelly
    if k.fraction > 0:
        ax1.axvline(k.fraction, color="#4CAF50", linestyle="--", alpha=0.7, label=f"f* = {k.fraction:.3f}")
        ax1.axvline(k.half_fraction, color="#FFC107", linestyle="--", alpha=0.7, label=f"f*/2 = {k.half_fraction:.3f}")

    ax1.set_xlabel("Kelly Fraction")
    ax1.set_ylabel("Total Return (%)", color="#2196F3")
    ax2.set_ylabel("Sharpe Ratio", color="#FF5722")
    ax1.tick_params(axis="y", labelcolor="#2196F3")
    ax2.tick_params(axis="y", labelcolor="#FF5722")

    # Combined legend
    lines1, labels1 = ax1.get_legend_handles_labels()
    lines2, labels2 = ax2.get_legend_handles_labels()
    ax1.legend(lines1 + lines2, labels1 + labels2, loc="best")

    ax1.set_title(f"{sym} — Kelly Fraction Sensitivity")
    ax1.grid(True, alpha=0.3)
    fig.tight_layout()

    sens_path = output_dir / f"{sym}_kelly_sensitivity.png"
    fig.savefig(sens_path, dpi=150)
    plt.close(fig)
    saved.append(sens_path)
    print(f"  Saved: {sens_path}")

    return saved


def parse_args():
    parser = argparse.ArgumentParser(
        description="Kelly Criterion Position Sizing Backtest",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python -m trading_system.backtests.kelly_sizing
  python -m trading_system.backtests.kelly_sizing --symbols BTC SPY --chart
  python -m trading_system.backtests.kelly_sizing --symbols BTC --days 500 --chart
        """,
    )
    parser.add_argument(
        "--symbols",
        nargs="+",
        default=list(DEFAULT_POSITIONS.keys()),
        help="Symbols to backtest (default: all default positions)",
    )
    parser.add_argument(
        "--days",
        type=int,
        default=756,
        help="Number of daily bars to fetch (default: 756 ≈ 3 years)",
    )
    parser.add_argument(
        "--chart",
        action="store_true",
        help="Generate matplotlib charts",
    )
    parser.add_argument(
        "--force-refresh",
        action="store_true",
        help="Force re-fetch data from API (ignore cache)",
    )
    parser.add_argument(
        "--api-key",
        default=None,
        help="Twelve Data API key (or set TWELVE_DATA_API_KEY env var)",
    )
    return parser.parse_args()


def main():
    load_dotenv()
    args = parse_args()

    api_key = args.api_key or os.getenv(
        "TWELVE_DATA_API_KEY", "f2c57fbb0a794024b0defff74af45686"
    )

    symbols = args.symbols
    positions = parse_positions(None, symbols)

    print("=" * 64)
    print("Kelly Criterion Position Sizing Backtest")
    print("=" * 64)
    print(f"Symbols: {', '.join(symbols)}")
    print(f"Days: {args.days}")
    print()

    # Load data
    print("Loading market data...")
    all_bars = load_daily_data(
        symbols=symbols,
        api_key=api_key,
        outputsize=args.days,
        force_refresh=args.force_refresh,
    )
    print()

    # Run Kelly comparison for each symbol
    for sym in symbols:
        if sym not in all_bars:
            print(f"{sym}: No data available, skipping")
            continue

        bars = all_bars[sym]
        shares, price = positions[sym]

        print(f"Running Kelly analysis for {sym} ({shares} shares @ ${price:.2f})...")
        comp = run_kelly_comparison(bars, sym, shares, price)
        print()
        print_kelly_summary(comp)
        print()

        if args.chart:
            print(f"Generating charts for {sym}...")
            plot_kelly_comparison(comp, bars, shares, price)
            print()

    print("Done.")


if __name__ == "__main__":
    main()
