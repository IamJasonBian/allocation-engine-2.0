"""
CLI entry point for the Pairwise DCA backtest.

Usage:
    python -m trading_system.backtests.pairwise_dca_backtest
    python -m trading_system.backtests.pairwise_dca_backtest --symbols BTC SPY --days 756 --sensitivity
"""

import argparse
import os
import sys

from dotenv import load_dotenv

from trading_system.backtests.data_loader import load_daily_data
from trading_system.backtests.metrics import (
    BacktestMetrics,
    compute_metrics,
    parameter_sensitivity,
)
from trading_system.backtests.report import generate_pdf, generate_report
from trading_system.backtests.simulation import SimulationResult, run_simulation

# Default positions matching live portfolio
DEFAULT_POSITIONS = {
    "BTC": (3262, 31.0),
    "SPY": (100, 450.0),
    "QQQ": (100, 450.0),
    "AMZN": (50, 200.0),
}

# Sensitivity parameter ranges
SENSITIVITY_PARAMS = {
    "stop_offset_pct": [0.005, 0.0075, 0.01, 0.015, 0.02, 0.03],
    "buy_offset": [0.10, 0.15, 0.20, 0.30, 0.40, 0.50],
    "coverage_threshold": [0.10, 0.15, 0.20, 0.25, 0.30],
}


def parse_args():
    parser = argparse.ArgumentParser(
        description="Pairwise DCA Strategy Backtest",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python -m trading_system.backtests.pairwise_dca_backtest
  python -m trading_system.backtests.pairwise_dca_backtest --symbols BTC SPY --days 756
  python -m trading_system.backtests.pairwise_dca_backtest --sensitivity
  python -m trading_system.backtests.pairwise_dca_backtest --positions BTC:3262:31 SPY:100:450
        """,
    )
    parser.add_argument(
        "--symbols",
        nargs="+",
        default=list(DEFAULT_POSITIONS.keys()),
        help="Symbols to backtest (default: BTC SPY QQQ AMZN)",
    )
    parser.add_argument(
        "--days",
        type=int,
        default=756,
        help="Number of daily bars to fetch (default: 756 ≈ 3 years)",
    )
    parser.add_argument(
        "--positions",
        nargs="+",
        default=None,
        help="Override positions as SYMBOL:SHARES:PRICE (e.g., BTC:3262:31 SPY:100:450)",
    )
    parser.add_argument(
        "--sensitivity",
        action="store_true",
        help="Run parameter sensitivity analysis",
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


def parse_positions(position_args, symbols):
    """Parse --positions args into {symbol: (shares, price)} dict."""
    positions = {}
    if position_args:
        for p in position_args:
            parts = p.split(":")
            if len(parts) == 3:
                sym, shares, price = parts
                positions[sym] = (int(shares), float(price))
            elif len(parts) == 2:
                sym, shares = parts
                positions[sym] = (int(shares), DEFAULT_POSITIONS.get(sym, (100, 100.0))[1])
            else:
                print(f"WARNING: Invalid position format '{p}', expected SYMBOL:SHARES:PRICE")
    else:
        for sym in symbols:
            if sym in DEFAULT_POSITIONS:
                positions[sym] = DEFAULT_POSITIONS[sym]
            else:
                positions[sym] = (100, 100.0)
    return positions


def main():
    load_dotenv()
    args = parse_args()

    api_key = args.api_key or os.getenv(
        "TWELVE_DATA_API_KEY", "f2c57fbb0a794024b0defff74af45686"
    )

    symbols = args.symbols
    positions = parse_positions(args.positions, symbols)

    print("=" * 70)
    print("Pairwise DCA Strategy Backtest")
    print("=" * 70)
    print(f"Symbols: {', '.join(symbols)}")
    print(f"Days: {args.days}")
    print(f"Sensitivity: {'Yes' if args.sensitivity else 'No'}")
    print()

    # ── Step 1: Load data ────────────────────────────────────────────────
    print("Step 1: Loading market data...")
    all_bars = load_daily_data(
        symbols=symbols,
        api_key=api_key,
        outputsize=args.days,
        force_refresh=args.force_refresh,
    )
    print()

    # ── Step 2: Run simulations ──────────────────────────────────────────
    print("Step 2: Running simulations...")
    sim_results = {}
    metrics_results = {}

    for sym in symbols:
        if sym not in all_bars:
            print(f"  {sym}: No data available, skipping")
            continue

        bars = all_bars[sym]
        shares, price = positions[sym]
        print(f"  {sym}: {shares} shares @ ${price:.2f}, {len(bars)} bars")

        result = run_simulation(
            bars=bars,
            symbol=sym,
            initial_shares=shares,
            initial_price=price,
        )
        sim_results[sym] = result

        m = compute_metrics(result)
        metrics_results[sym] = m

        print(f"    Return: {m.total_return_pct:+.2f}% (B&H: {m.buy_hold_return_pct:+.2f}%)")
        print(f"    Sharpe: {m.sharpe_ratio:.3f}, Max DD: {m.max_drawdown_pct:.2f}%")
        print(f"    Pairs: {m.pairs_placed} placed, {m.pairs_completed} completed, "
              f"{m.pairs_sell_only} sell-only, {m.pairs_cancelled} cancelled")
    print()

    # ── Step 3: Sensitivity analysis (optional) ──────────────────────────
    sensitivity_results = None
    if args.sensitivity:
        print("Step 3: Running parameter sensitivity analysis...")
        sensitivity_results = {}
        for sym in sim_results:
            bars = all_bars[sym]
            shares, price = positions[sym]
            sensitivity_results[sym] = {}

            for param_name, param_values in SENSITIVITY_PARAMS.items():
                print(f"  {sym} / {param_name}: testing {len(param_values)} values...")
                sens = parameter_sensitivity(
                    bars=bars,
                    symbol=sym,
                    initial_shares=shares,
                    initial_price=price,
                    param_name=param_name,
                    param_values=param_values,
                )
                sensitivity_results[sym][param_name] = sens
        print()

    # ── Step N: Generate report ──────────────────────────────────────────
    step_num = 4 if args.sensitivity else 3
    print(f"Step {step_num}: Generating report, charts, and PDF...")
    generate_report(sim_results, metrics_results, sensitivity_results)
    pdf_path = generate_pdf(sim_results, metrics_results, sensitivity_results)

    print()
    print("=" * 70)
    print("Backtest complete!")
    print(f"PDF: {pdf_path}")
    print("=" * 70)


if __name__ == "__main__":
    main()


# Allow running as: python -m trading_system.backtests.pairwise_dca_backtest
