"""
CLI entry point for the BTC DCA + Options Hedging comparison backtest.

Usage:
    python -m trading_system.backtests.strategy_comparison_backtest
    python -m trading_system.backtests.strategy_comparison_backtest --iv 0.25 --windows 126 63 21
"""

import argparse
import math
import os
import sys

from dotenv import load_dotenv

from trading_system.backtests.comparison_report import generate_comparison_pdf
from trading_system.backtests.data_loader import load_daily_data
from trading_system.backtests.drawdown_strategies import (
    run_adaptive_sizing_strategy,
    run_cash_reserve_strategy,
    run_trailing_stop_strategy,
)
from trading_system.backtests.metrics import BacktestMetrics, compute_metrics
from trading_system.backtests.options_metrics import (
    OptionsBacktestMetrics,
    compute_options_metrics,
)
from trading_system.backtests.options_simulation import (
    OptionsStrategyType,
    run_options_simulation,
)
from trading_system.backtests.parameter_optimizer import (
    compute_rolling_metrics,
    detect_drift,
    grid_search_params,
    suggest_regime_params,
)
from trading_system.backtests.simulation import SimulationResult, run_simulation

# BTC position (Grayscale Bitcoin Mini Trust ETF)
BTC_POSITION = (3262, 31.0)  # shares, price

# Options strategies: (underlying_symbol, strategy_type, label)
OPTIONS_STRATEGIES = [
    ("SPY", OptionsStrategyType.PROTECTIVE_PUT, "SPY_PUT"),
    ("IWM", OptionsStrategyType.PROTECTIVE_PUT, "IWM_PUT"),
    ("SPY", OptionsStrategyType.COLLAR, "SPY_COLLAR"),
]

# Default time windows in trading days
DEFAULT_WINDOWS = [126, 63, 21]  # 6mo, 3mo, 1mo


def parse_args():
    parser = argparse.ArgumentParser(
        description="BTC DCA + Options Hedging Comparison Backtest",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python -m trading_system.backtests.strategy_comparison_backtest
  python -m trading_system.backtests.strategy_comparison_backtest --iv 0.25
  python -m trading_system.backtests.strategy_comparison_backtest --windows 126 63 21
        """,
    )
    parser.add_argument(
        "--windows",
        nargs="+",
        type=int,
        default=DEFAULT_WINDOWS,
        help="Time windows in trading days (default: 126 63 21)",
    )
    parser.add_argument(
        "--iv",
        type=float,
        default=0.20,
        help="Implied volatility for options pricing (default: 0.20)",
    )
    parser.add_argument(
        "--otm-pct",
        type=float,
        default=0.05,
        help="OTM percentage for strike selection (default: 0.05)",
    )
    parser.add_argument(
        "--roll-days",
        type=int,
        default=21,
        help="Trading days between option rolls (default: 21)",
    )
    parser.add_argument(
        "--options-shares",
        type=int,
        default=100,
        help="Shares per options contract (default: 100)",
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


def _compute_combined_metrics(dates, values, risk_free_rate=0.05):
    """Compute return/Sharpe/drawdown from a list of daily portfolio values."""
    if len(values) < 2 or values[0] <= 0:
        return {
            "total_return_pct": 0.0,
            "annualized_return_pct": 0.0,
            "sharpe_ratio": 0.0,
            "max_drawdown_pct": 0.0,
        }

    initial = values[0]
    final = values[-1]
    total_return = (final - initial) / initial * 100

    trading_days = len(values)
    years = trading_days / 252
    if years > 0 and final > 0:
        annualized = ((final / initial) ** (1 / years) - 1) * 100
    else:
        annualized = 0.0

    # Daily returns
    daily_returns = []
    for i in range(1, len(values)):
        if values[i - 1] > 0:
            daily_returns.append((values[i] - values[i - 1]) / values[i - 1])

    # Sharpe
    sharpe = 0.0
    if len(daily_returns) >= 2:
        mean_r = sum(daily_returns) / len(daily_returns)
        var = sum((r - mean_r) ** 2 for r in daily_returns) / (len(daily_returns) - 1)
        std = math.sqrt(var) if var > 0 else 0.0
        if std > 0:
            daily_rf = risk_free_rate / 252
            sharpe = (mean_r - daily_rf) / std * math.sqrt(252)

    # Max drawdown
    peak = values[0]
    max_dd = 0.0
    for v in values:
        if v > peak:
            peak = v
        dd = (peak - v) / peak * 100 if peak > 0 else 0.0
        if dd > max_dd:
            max_dd = dd

    return {
        "total_return_pct": round(total_return, 2),
        "annualized_return_pct": round(annualized, 2),
        "sharpe_ratio": round(sharpe, 3),
        "max_drawdown_pct": round(max_dd, 2),
    }


def main():
    load_dotenv()
    args = parse_args()

    api_key = args.api_key or os.getenv(
        "TWELVE_DATA_API_KEY", "f2c57fbb0a794024b0defff74af45686"
    )

    windows = sorted(args.windows, reverse=True)  # Largest first
    max_bars = max(windows) + 10  # Extra buffer

    print("=" * 70)
    print("BTC DCA + Options Hedging Comparison Backtest")
    print("=" * 70)
    print(f"Windows: {[f'{w}d' for w in windows]}")
    print(f"IV: {args.iv:.0%}, OTM: {args.otm_pct:.0%}, Roll: {args.roll_days}d")
    print()

    # ── Step 1: Load data ────────────────────────────────────────────────
    print("Step 1: Loading market data...")
    symbols_needed = ["BTC", "SPY", "IWM", "QQQ"]
    all_bars = load_daily_data(
        symbols=symbols_needed,
        api_key=api_key,
        outputsize=max_bars,
        force_refresh=args.force_refresh,
    )

    # Verify we got data
    for sym in symbols_needed:
        if sym not in all_bars:
            print(f"ERROR: No data for {sym}. Cannot proceed.")
            sys.exit(1)
        print(f"  {sym}: {len(all_bars[sym])} bars loaded")
    print()

    # ── Step 2: Run BTC DCA per window ───────────────────────────────────
    print("Step 2: Running BTC DCA simulations...")
    all_results = {"dca": {}, "options": {}, "combined": {},
                   "drawdown": {}, "benchmarks": {},
                   "grid_search": None, "rolling_metrics": None,
                   "drift_alerts": None, "regime_suggestions": None}
    btc_shares, btc_price = BTC_POSITION

    for window in windows:
        bars = all_bars["BTC"][-window:]
        if len(bars) < window:
            print(f"  WARNING: Only {len(bars)} bars for {window}d window")

        result = run_simulation(
            bars=bars,
            symbol="BTC",
            initial_shares=btc_shares,
            initial_price=btc_price,
        )
        m = compute_metrics(result)

        label = f"{window}d"
        print(f"  {label}: Return {m.total_return_pct:+.2f}%, Sharpe {m.sharpe_ratio:.3f}, "
              f"MaxDD {m.max_drawdown_pct:.2f}%, Pairs {m.pairs_completed}/{m.pairs_placed}")

        all_results["dca"][window] = {"result": result, "metrics": m}
    print()

    # ── Step 2b: Run drawdown strategies per window ──────────────────────
    print("Step 2b: Running drawdown strategies...")
    drawdown_strats = {
        "Cash Reserve": run_cash_reserve_strategy,
        "Trailing Stop": run_trailing_stop_strategy,
        "Adaptive Sizing": run_adaptive_sizing_strategy,
    }

    for strat_name, strat_fn in drawdown_strats.items():
        all_results["drawdown"][strat_name] = {}
        for window in windows:
            bars = all_bars["BTC"][-window:]
            dr = strat_fn(bars=bars, shares=btc_shares, price=btc_price)
            cm = _compute_combined_metrics(dr.dates, dr.values)

            label = f"{window}d"
            print(f"  {strat_name} {label}: Return {cm['total_return_pct']:+.2f}%, "
                  f"Sharpe {cm['sharpe_ratio']:.3f}, MaxDD {cm['max_drawdown_pct']:.2f}%")

            all_results["drawdown"][strat_name][window] = {
                "result": dr,
                "metrics": cm,
            }
    print()

    # ── Step 2c: Compute SPY and QQQ buy-and-hold benchmarks ─────────────
    print("Step 2c: Computing growth benchmarks (SPY, QQQ)...")
    for bench_sym in ["SPY", "QQQ"]:
        all_results["benchmarks"][bench_sym] = {}
        for window in windows:
            bench_bars = all_bars[bench_sym][-window:]
            if not bench_bars:
                continue

            initial_price_bench = bench_bars[0]["close"]
            # Normalize to same initial capital as BTC DCA for comparison
            btc_initial_value = btc_shares * btc_price
            bench_shares = btc_initial_value / initial_price_bench

            bench_dates = [b["date"] for b in bench_bars]
            bench_values = [bench_shares * b["close"] for b in bench_bars]
            bm = _compute_combined_metrics(bench_dates, bench_values)

            label = f"{window}d"
            print(f"  {bench_sym} {label}: Return {bm['total_return_pct']:+.2f}%, "
                  f"Sharpe {bm['sharpe_ratio']:.3f}")

            all_results["benchmarks"][bench_sym][window] = {
                "dates": bench_dates,
                "values": bench_values,
                "metrics": bm,
            }
    print()

    # ── Step 3: Run options strategies per window ────────────────────────
    print("Step 3: Running options strategies...")
    for underlying_sym, strat_type, strat_label in OPTIONS_STRATEGIES:
        all_results["options"][strat_label] = {}

        for window in windows:
            bars = all_bars[underlying_sym][-window:]
            initial_price_opt = bars[0]["close"]

            opt_result = run_options_simulation(
                bars=bars,
                symbol=underlying_sym,
                strategy_type=strat_type,
                initial_shares=args.options_shares,
                initial_price=initial_price_opt,
                iv=args.iv,
                otm_pct=args.otm_pct,
                roll_period_days=args.roll_days,
            )
            opt_metrics = compute_options_metrics(opt_result)

            label = f"{window}d"
            print(f"  {strat_label} {label}: Return {opt_metrics.total_return_pct:+.2f}%, "
                  f"Net Cost ${opt_metrics.net_premium_cost:,.2f}, Rolls {opt_metrics.num_rolls}")

            all_results["options"][strat_label][window] = {
                "result": opt_result,
                "metrics": opt_metrics,
            }
    print()

    # ── Step 4: Compute combined BTC DCA + hedge portfolios ──────────────
    print("Step 4: Computing combined portfolios...")
    for _, _, strat_label in OPTIONS_STRATEGIES:
        combo_key = f"BTC_DCA+{strat_label}"
        all_results["combined"][combo_key] = {}

        for window in windows:
            if window not in all_results["dca"]:
                continue
            if window not in all_results["options"].get(strat_label, {}):
                continue

            dca_snaps = all_results["dca"][window]["result"].snapshots
            opt_snaps = all_results["options"][strat_label][window]["result"].snapshots

            # Both should have same number of bars, but use min length
            n = min(len(dca_snaps), len(opt_snaps))

            # Options P&L = net_portfolio_value - initial_equity_value
            opt_initial_equity = opt_snaps[0].equity_value

            dates = []
            combined_values = []
            for i in range(n):
                dca_val = dca_snaps[i].net_value
                # Options P&L: change in net portfolio value from start
                opt_pnl = opt_snaps[i].net_portfolio_value - opt_initial_equity
                combined = dca_val + opt_pnl
                dates.append(dca_snaps[i].date)
                combined_values.append(combined)

            cm = _compute_combined_metrics(dates, combined_values)
            label = f"{window}d"
            print(f"  {combo_key} {label}: Return {cm['total_return_pct']:+.2f}%, "
                  f"Sharpe {cm['sharpe_ratio']:.3f}")

            all_results["combined"][combo_key][window] = {
                "dates": dates,
                "values": combined_values,
                "metrics": cm,
            }
    print()

    # ── Step 4b: Grid search on longest window ───────────────────────────
    longest_window = windows[0]
    print(f"Step 4b: Grid search optimization ({longest_window}d window)...")
    grid_bars = all_bars["BTC"][-longest_window:]
    gs = grid_search_params(
        bars=grid_bars,
        symbol="BTC",
        shares=btc_shares,
        price=btc_price,
    )
    all_results["grid_search"] = gs
    print(f"  Best params: stop_offset={gs.best['stop_offset_pct']:.4f}, "
          f"buy_offset={gs.best['buy_offset']:.2f}, "
          f"coverage={gs.best['coverage_threshold']:.2f}")
    print(f"  Best Sharpe: {gs.best['sharpe_ratio']:.3f}, "
          f"Return: {gs.best['total_return_pct']:+.2f}%")
    print()

    # ── Step 4c: Rolling metrics + drift detection ───────────────────────
    print(f"Step 4c: Computing rolling metrics & drift detection ({longest_window}d)...")
    longest_snaps = all_results["dca"][longest_window]["result"].snapshots
    rolling = compute_rolling_metrics(longest_snaps, window=21)
    all_results["rolling_metrics"] = rolling

    drift = detect_drift(rolling)
    all_results["drift_alerts"] = drift
    print(f"  Rolling metrics: {len(rolling)} data points")
    print(f"  Drift alerts: {len(drift)} alerts detected")

    # Regime suggestion based on latest rolling vol
    if rolling:
        latest_vol = rolling[-1]["rolling_vol"]
        regime = suggest_regime_params(latest_vol)
        all_results["regime_suggestions"] = regime
        print(f"  Current regime: {regime['regime']} (vol={latest_vol:.1f}%)")
    print()

    # ── Step 5: Generate comparison PDF ──────────────────────────────────
    print("Step 5: Generating comparison report and PDF...")
    pdf_path = generate_comparison_pdf(all_results, windows)

    print()
    print("=" * 70)
    print("Comparison backtest complete!")
    print(f"PDF: {pdf_path}")
    print("=" * 70)


if __name__ == "__main__":
    main()
