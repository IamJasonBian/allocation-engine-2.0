"""
Comparison report generator — produces a single PDF comparing BTC DCA
across multiple time windows with options-based hedging overlays,
drawdown strategies, benchmarks, risk analysis, and parameter optimization.
"""

from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from trading_system.backtests.metrics import BacktestMetrics
from trading_system.backtests.options_metrics import OptionsBacktestMetrics
from trading_system.backtests.pdf_helpers import (
    PaperPDF,
    pdf_body,
    pdf_bullet,
    pdf_embed_chart,
    pdf_section,
    pdf_subsection,
    pdf_table_header,
    pdf_table_row,
    safe_text,
)

DOCS_DIR = Path(__file__).parent.parent.parent / "docs"
CHARTS_DIR = DOCS_DIR / "charts"

WINDOW_LABELS = {126: "6mo", 63: "3mo", 21: "1mo"}


def generate_comparison_pdf(
    all_results: Dict[str, Any],
    time_windows: List[int],
) -> Path:
    """
    Generate the multi-strategy comparison PDF.

    Args:
        all_results: Nested dict with keys:
            dca, options, combined, drawdown, benchmarks,
            grid_search, rolling_metrics, drift_alerts, regime_suggestions
        time_windows: List of window sizes in trading days [126, 63, 21]

    Returns:
        Path to generated PDF
    """
    CHARTS_DIR.mkdir(parents=True, exist_ok=True)

    # Generate all charts first
    _generate_charts(all_results, time_windows)

    # Build PDF
    pdf = PaperPDF(title="BTC DCA + Options Hedging Comparison")
    pdf.set_auto_page_break(auto=True, margin=20)

    # 1. Title page + master table
    _title_page(pdf, all_results, time_windows)
    # 2. BTC DCA Results
    _dca_section(pdf, all_results, time_windows)
    # 3. Growth Benchmarks (NEW)
    _benchmarks_section(pdf, all_results, time_windows)
    # 4. Drawdown Strategies (NEW)
    _drawdown_section(pdf, all_results, time_windows)
    # 5-7. BTC DCA + hedge strategies
    _hedge_section(pdf, all_results, time_windows, "SPY_PUT", "5. BTC DCA + SPY Protective Put")
    _hedge_section(pdf, all_results, time_windows, "IWM_PUT", "6. BTC DCA + IWM Protective Put")
    _hedge_section(pdf, all_results, time_windows, "SPY_COLLAR", "7. BTC DCA + SPY Collar")
    # 8. Standalone Options
    _standalone_options_section(pdf, all_results, time_windows)
    # 9. Options Cost Analysis
    _options_cost_section(pdf, all_results, time_windows)
    # 10. Risk Analysis (NEW)
    _risk_analysis_section(pdf)
    # 11. Backtest Limitations (NEW)
    _backtest_limitations_section(pdf)
    # 12. Parameter Optimization (NEW)
    _parameter_optimization_section(pdf, all_results)
    # 13. Parameter Monitoring & Drift Detection (NEW)
    _drift_detection_section(pdf, all_results)
    # 14. Methodology
    _methodology_section(pdf, all_results)
    # 15. Conclusion
    _conclusion_section(pdf, all_results, time_windows)

    pdf_path = DOCS_DIR / "btc_dca_hedging_comparison.pdf"
    pdf.output(str(pdf_path))
    print(f"PDF written to {pdf_path}")
    return pdf_path


# ── Chart generation ─────────────────────────────────────────────────────


def _generate_charts(all_results: Dict, time_windows: List[int]):
    """Generate all matplotlib charts for the comparison report."""
    import matplotlib.pyplot as plt
    import matplotlib.dates as mdates

    # 1. Normalized return curves — one plot per window, all strategies
    for window in time_windows:
        label = WINDOW_LABELS.get(window, f"{window}d")
        fig, ax = plt.subplots(figsize=(12, 6))

        # BTC DCA
        if "dca" in all_results and window in all_results["dca"]:
            snaps = all_results["dca"][window]["result"].snapshots
            _plot_normalized(ax, snaps, "BTC DCA", use_net_value=True)

        # Combined strategies
        if "combined" in all_results:
            for combo_name, windows_data in all_results["combined"].items():
                if window in windows_data:
                    dates, values = windows_data[window]["dates"], windows_data[window]["values"]
                    if values and values[0] != 0:
                        normed = [v / values[0] * 100 for v in values]
                        parsed_dates = [datetime.strptime(d, "%Y-%m-%d") for d in dates]
                        ax.plot(parsed_dates, normed, label=combo_name.replace("_", " "), linewidth=1.2)

        ax.set_title(f"Strategy Comparison -- {label} Window")
        ax.set_ylabel("Normalized Value (100 = start)")
        ax.axhline(y=100, color="gray", linestyle="--", alpha=0.5, linewidth=0.8)
        ax.legend(fontsize=8, loc="best")
        ax.grid(True, alpha=0.3)
        ax.xaxis.set_major_formatter(mdates.DateFormatter("%Y-%m"))
        fig.autofmt_xdate()
        fig.tight_layout()
        fig.savefig(CHARTS_DIR / f"comparison_normalized_{label}.png", dpi=150)
        plt.close(fig)

    # 2. Per-strategy equity curves
    for window in time_windows:
        label = WINDOW_LABELS.get(window, f"{window}d")

        # DCA equity curve
        if "dca" in all_results and window in all_results["dca"]:
            snaps = all_results["dca"][window]["result"].snapshots
            fig, ax = plt.subplots(figsize=(12, 5))
            dates = [datetime.strptime(s.date, "%Y-%m-%d") for s in snaps]
            vals = [s.net_value for s in snaps]
            ax.plot(dates, vals, label="BTC DCA", linewidth=1.5, color="#2980b9")
            ax.set_title(f"BTC DCA Equity -- {label}")
            ax.set_ylabel("Portfolio Value ($)")
            ax.legend()
            ax.grid(True, alpha=0.3)
            ax.xaxis.set_major_formatter(mdates.DateFormatter("%Y-%m"))
            fig.autofmt_xdate()
            fig.tight_layout()
            fig.savefig(CHARTS_DIR / f"dca_equity_{label}.png", dpi=150)
            plt.close(fig)

        # Options standalone equity curves
        if "options" in all_results:
            for strat_name, windows_data in all_results["options"].items():
                if window in windows_data:
                    snaps = windows_data[window]["result"].snapshots
                    fig, ax = plt.subplots(figsize=(12, 5))
                    dates = [datetime.strptime(s.date, "%Y-%m-%d") for s in snaps]
                    vals = [s.net_portfolio_value for s in snaps]
                    ax.plot(dates, vals, linewidth=1.5, color="#e67e22")
                    ax.set_title(f"{strat_name.replace('_', ' ')} -- {label}")
                    ax.set_ylabel("Net Portfolio Value ($)")
                    ax.grid(True, alpha=0.3)
                    ax.xaxis.set_major_formatter(mdates.DateFormatter("%Y-%m"))
                    fig.autofmt_xdate()
                    fig.tight_layout()
                    fig.savefig(CHARTS_DIR / f"options_{strat_name}_{label}.png", dpi=150)
                    plt.close(fig)

    # 3. Combined hedge equity curves
    if "combined" in all_results:
        for combo_name, windows_data in all_results["combined"].items():
            for window in time_windows:
                if window not in windows_data:
                    continue
                label = WINDOW_LABELS.get(window, f"{window}d")
                dates_str = windows_data[window]["dates"]
                values = windows_data[window]["values"]
                dates = [datetime.strptime(d, "%Y-%m-%d") for d in dates_str]

                fig, ax = plt.subplots(figsize=(12, 5))
                ax.plot(dates, values, linewidth=1.5, color="#27ae60",
                        label=combo_name.replace("_", " "))

                # Also plot plain DCA for comparison
                if "dca" in all_results and window in all_results["dca"]:
                    dca_snaps = all_results["dca"][window]["result"].snapshots
                    dca_dates = [datetime.strptime(s.date, "%Y-%m-%d") for s in dca_snaps]
                    dca_vals = [s.net_value for s in dca_snaps]
                    ax.plot(dca_dates, dca_vals, linewidth=1.2, color="#2980b9",
                            linestyle="--", alpha=0.7, label="BTC DCA (unhedged)")

                ax.set_title(f"{combo_name.replace('_', ' ')} -- {label}")
                ax.set_ylabel("Combined Portfolio Value ($)")
                ax.legend()
                ax.grid(True, alpha=0.3)
                ax.xaxis.set_major_formatter(mdates.DateFormatter("%Y-%m"))
                fig.autofmt_xdate()
                fig.tight_layout()
                safe_name = combo_name.replace("+", "_plus_")
                fig.savefig(CHARTS_DIR / f"combined_{safe_name}_{label}.png", dpi=150)
                plt.close(fig)

    # 4. Options roll cost waterfall
    if "options" in all_results:
        for window in time_windows:
            label = WINDOW_LABELS.get(window, f"{window}d")
            strat_names = []
            premiums_paid = []
            premiums_received = []
            intrinsic_recovered = []

            for strat_name, windows_data in all_results["options"].items():
                if window not in windows_data:
                    continue
                m = windows_data[window]["metrics"]
                strat_names.append(strat_name.replace("_", " "))
                premiums_paid.append(m.total_premium_paid)
                premiums_received.append(m.total_premium_received)
                intrinsic_recovered.append(m.total_intrinsic_recovered)

            if not strat_names:
                continue

            fig, ax = plt.subplots(figsize=(10, 5))
            x = range(len(strat_names))
            width = 0.25
            ax.bar([i - width for i in x], premiums_paid, width, label="Premium Paid", color="#e74c3c")
            ax.bar(list(x), premiums_received, width, label="Premium Received", color="#27ae60")
            ax.bar([i + width for i in x], intrinsic_recovered, width, label="Intrinsic Recovered", color="#3498db")
            ax.set_xticks(list(x))
            ax.set_xticklabels(strat_names, fontsize=9)
            ax.set_ylabel("Dollars ($)")
            ax.set_title(f"Options Cost Waterfall -- {label}")
            ax.legend()
            ax.grid(True, alpha=0.3, axis="y")
            fig.tight_layout()
            fig.savefig(CHARTS_DIR / f"options_waterfall_{label}.png", dpi=150)
            plt.close(fig)

    # 5. Benchmark normalized charts (BTC DCA vs SPY vs QQQ)
    if "benchmarks" in all_results:
        for window in time_windows:
            label = WINDOW_LABELS.get(window, f"{window}d")
            fig, ax = plt.subplots(figsize=(12, 6))

            # BTC DCA
            if "dca" in all_results and window in all_results["dca"]:
                snaps = all_results["dca"][window]["result"].snapshots
                _plot_normalized(ax, snaps, "BTC DCA", use_net_value=True)

            # SPY and QQQ
            colors = {"SPY": "#e67e22", "QQQ": "#8e44ad"}
            for bench_sym in ["SPY", "QQQ"]:
                if bench_sym in all_results["benchmarks"] and window in all_results["benchmarks"][bench_sym]:
                    bdata = all_results["benchmarks"][bench_sym][window]
                    dates = [datetime.strptime(d, "%Y-%m-%d") for d in bdata["dates"]]
                    vals = bdata["values"]
                    if vals and vals[0] != 0:
                        normed = [v / vals[0] * 100 for v in vals]
                        ax.plot(dates, normed, label=f"{bench_sym} Buy & Hold",
                                linewidth=1.5, color=colors.get(bench_sym, "#333"))

            ax.set_title(f"BTC DCA vs Growth Benchmarks -- {label}")
            ax.set_ylabel("Normalized Value (100 = start)")
            ax.axhline(y=100, color="gray", linestyle="--", alpha=0.5, linewidth=0.8)
            ax.legend(fontsize=9, loc="best")
            ax.grid(True, alpha=0.3)
            ax.xaxis.set_major_formatter(mdates.DateFormatter("%Y-%m"))
            fig.autofmt_xdate()
            fig.tight_layout()
            fig.savefig(CHARTS_DIR / f"benchmark_normalized_{label}.png", dpi=150)
            plt.close(fig)

    # 6. Drawdown strategies charts
    if "drawdown" in all_results:
        colors_dd = {"Cash Reserve": "#e74c3c", "Trailing Stop": "#f39c12", "Adaptive Sizing": "#1abc9c"}
        for window in time_windows:
            label = WINDOW_LABELS.get(window, f"{window}d")
            fig, ax = plt.subplots(figsize=(12, 6))

            # BTC DCA baseline
            if "dca" in all_results and window in all_results["dca"]:
                snaps = all_results["dca"][window]["result"].snapshots
                _plot_normalized(ax, snaps, "BTC DCA (baseline)", use_net_value=True)

            for strat_name, windows_data in all_results["drawdown"].items():
                if window in windows_data:
                    dr = windows_data[window]["result"]
                    if dr.values and dr.values[0] != 0:
                        normed = [v / dr.values[0] * 100 for v in dr.values]
                        dates = [datetime.strptime(d, "%Y-%m-%d") for d in dr.dates]
                        ax.plot(dates, normed, label=strat_name, linewidth=1.5,
                                color=colors_dd.get(strat_name, "#333"))

            ax.set_title(f"Drawdown Strategies -- {label}")
            ax.set_ylabel("Normalized Value (100 = start)")
            ax.axhline(y=100, color="gray", linestyle="--", alpha=0.5, linewidth=0.8)
            ax.legend(fontsize=9, loc="best")
            ax.grid(True, alpha=0.3)
            ax.xaxis.set_major_formatter(mdates.DateFormatter("%Y-%m"))
            fig.autofmt_xdate()
            fig.tight_layout()
            fig.savefig(CHARTS_DIR / f"drawdown_strategies_{label}.png", dpi=150)
            plt.close(fig)

    # 7. Grid search heatmap
    if all_results.get("grid_search"):
        _generate_grid_search_chart(all_results["grid_search"], plt)

    # 8. Rolling metrics charts
    if all_results.get("rolling_metrics"):
        _generate_rolling_charts(all_results, plt, mdates)


def _generate_grid_search_chart(gs, plt):
    """Generate grid search heatmap: Sharpe as function of stop_offset x buy_offset."""
    import numpy as np

    rows = gs.rows
    # Extract unique values
    stop_offsets = sorted(set(r["stop_offset_pct"] for r in rows))
    buy_offsets = sorted(set(r["buy_offset"] for r in rows))

    # Average Sharpe across coverage values for each (stop, buy) pair
    grid = {}
    for r in rows:
        key = (r["stop_offset_pct"], r["buy_offset"])
        if key not in grid:
            grid[key] = []
        grid[key].append(r["sharpe_ratio"])

    heatmap = []
    for so in stop_offsets:
        row = []
        for bo in buy_offsets:
            vals = grid.get((so, bo), [0])
            row.append(sum(vals) / len(vals))
        heatmap.append(row)

    fig, ax = plt.subplots(figsize=(10, 7))
    data = np.array(heatmap)
    im = ax.imshow(data, cmap="RdYlGn", aspect="auto", origin="lower")
    ax.set_xticks(range(len(buy_offsets)))
    ax.set_xticklabels([f"${bo:.2f}" for bo in buy_offsets], fontsize=9)
    ax.set_yticks(range(len(stop_offsets)))
    ax.set_yticklabels([f"{so:.4f}" for so in stop_offsets], fontsize=9)
    ax.set_xlabel("Buy Offset ($)")
    ax.set_ylabel("Stop Offset (%)")
    ax.set_title("Grid Search: Average Sharpe Ratio\n(stop_offset x buy_offset, averaged over coverage)")

    # Annotate cells
    for i in range(len(stop_offsets)):
        for j in range(len(buy_offsets)):
            ax.text(j, i, f"{data[i, j]:.2f}", ha="center", va="center",
                    fontsize=8, color="black")

    fig.colorbar(im, label="Sharpe Ratio")
    fig.tight_layout()
    fig.savefig(CHARTS_DIR / "grid_search_heatmap.png", dpi=150)
    plt.close(fig)


def _generate_rolling_charts(all_results: Dict, plt, mdates):
    """Generate rolling Sharpe, completion rate, and volatility regime charts."""
    rolling = all_results["rolling_metrics"]
    drift = all_results.get("drift_alerts", [])

    dates = [datetime.strptime(r["date"], "%Y-%m-%d") for r in rolling]
    sharpes = [r["rolling_sharpe"] for r in rolling]
    completions = [r["rolling_completion_rate"] for r in rolling]
    vols = [r["rolling_vol"] for r in rolling]

    # Rolling Sharpe
    fig, ax = plt.subplots(figsize=(12, 5))
    ax.plot(dates, sharpes, linewidth=1.5, color="#2980b9", label="Rolling 21d Sharpe")
    ax.axhline(y=-1.0, color="#e74c3c", linestyle="--", linewidth=1.2, label="Drift threshold (-1.0)")
    ax.axhline(y=0, color="gray", linestyle=":", alpha=0.5)
    ax.set_title("Rolling 21-Day Sharpe Ratio")
    ax.set_ylabel("Sharpe Ratio")
    ax.legend(fontsize=9)
    ax.grid(True, alpha=0.3)
    ax.xaxis.set_major_formatter(mdates.DateFormatter("%Y-%m"))
    fig.autofmt_xdate()
    fig.tight_layout()
    fig.savefig(CHARTS_DIR / "rolling_sharpe.png", dpi=150)
    plt.close(fig)

    # Rolling completion rate
    fig, ax = plt.subplots(figsize=(12, 5))
    ax.plot(dates, completions, linewidth=1.5, color="#27ae60", label="Rolling 21d Coverage %")
    ax.axhline(y=50.0, color="#e74c3c", linestyle="--", linewidth=1.2, label="Drift threshold (50%)")
    ax.set_title("Rolling 21-Day Coverage Rate")
    ax.set_ylabel("Coverage %")
    ax.legend(fontsize=9)
    ax.grid(True, alpha=0.3)
    ax.xaxis.set_major_formatter(mdates.DateFormatter("%Y-%m"))
    fig.autofmt_xdate()
    fig.tight_layout()
    fig.savefig(CHARTS_DIR / "rolling_completion.png", dpi=150)
    plt.close(fig)

    # Rolling vol with regime bands
    fig, ax = plt.subplots(figsize=(12, 5))
    ax.plot(dates, vols, linewidth=1.5, color="#8e44ad", label="Rolling 21d Annualized Vol")
    ax.axhspan(0, 15, alpha=0.1, color="#27ae60", label="Low vol (<15%)")
    ax.axhspan(15, 30, alpha=0.1, color="#f39c12", label="Medium vol (15-30%)")
    ax.axhspan(30, max(vols) * 1.2 if vols else 50, alpha=0.1, color="#e74c3c", label="High vol (>30%)")
    ax.set_title("Rolling 21-Day Annualized Volatility with Regime Bands")
    ax.set_ylabel("Annualized Volatility (%)")
    ax.legend(fontsize=8, loc="upper left")
    ax.grid(True, alpha=0.3)
    ax.xaxis.set_major_formatter(mdates.DateFormatter("%Y-%m"))
    fig.autofmt_xdate()
    fig.tight_layout()
    fig.savefig(CHARTS_DIR / "rolling_vol_regimes.png", dpi=150)
    plt.close(fig)


def _plot_normalized(ax, snapshots, label: str, use_net_value: bool = True):
    """Plot normalized (base=100) return curve on an axes."""
    dates = [datetime.strptime(s.date, "%Y-%m-%d") for s in snapshots]
    if use_net_value:
        vals = [s.net_value for s in snapshots]
    else:
        vals = [s.net_portfolio_value for s in snapshots]
    if vals and vals[0] != 0:
        normed = [v / vals[0] * 100 for v in vals]
        ax.plot(dates, normed, label=label, linewidth=1.5)


# ── PDF sections ─────────────────────────────────────────────────────────


def _title_page(pdf, all_results: Dict, time_windows: List[int]):
    """Title page with master comparison table."""
    pdf.add_page()
    pdf.ln(30)
    pdf.set_font("Helvetica", "B", 26)
    pdf.cell(0, 15, "BTC DCA + Options Hedging", new_x="LMARGIN", new_y="NEXT", align="C")
    pdf.set_font("Helvetica", "", 18)
    pdf.cell(0, 12, "Multi-Timeframe Comparison", new_x="LMARGIN", new_y="NEXT", align="C")
    pdf.ln(10)
    pdf.set_font("Helvetica", "", 11)
    pdf.cell(0, 8, f"Generated {datetime.now().strftime('%Y-%m-%d')}",
             new_x="LMARGIN", new_y="NEXT", align="C")
    pdf.ln(15)

    # Master comparison table
    pdf.set_font("Helvetica", "B", 12)
    pdf.cell(0, 8, "Master Comparison", new_x="LMARGIN", new_y="NEXT")
    pdf.ln(2)

    col_widths = [42, 20, 24, 24, 24, 24, 20]
    headers = ["Strategy", "Window", "Return%", "Ann.Ret%", "Sharpe", "MaxDD%", "Rolls"]
    pdf_table_header(pdf, headers, col_widths)

    for window in time_windows:
        label = WINDOW_LABELS.get(window, f"{window}d")

        # BTC DCA row
        if "dca" in all_results and window in all_results["dca"]:
            m = all_results["dca"][window]["metrics"]
            pdf_table_row(pdf, [
                "BTC DCA", label,
                f"{m.total_return_pct:+.1f}", f"{m.annualized_return_pct:+.1f}",
                f"{m.sharpe_ratio:.3f}", f"{m.max_drawdown_pct:.1f}", "-",
            ], col_widths)

        # Benchmark rows
        if "benchmarks" in all_results:
            for bench_sym in ["SPY", "QQQ"]:
                if bench_sym in all_results["benchmarks"] and window in all_results["benchmarks"][bench_sym]:
                    bm = all_results["benchmarks"][bench_sym][window]["metrics"]
                    pdf_table_row(pdf, [
                        f"{bench_sym} Buy&Hold", label,
                        f"{bm['total_return_pct']:+.1f}",
                        f"{bm['annualized_return_pct']:+.1f}",
                        f"{bm['sharpe_ratio']:.3f}",
                        f"{bm['max_drawdown_pct']:.1f}", "-",
                    ], col_widths)

        # Drawdown strategy rows
        if "drawdown" in all_results:
            for strat_name, wdata in all_results["drawdown"].items():
                if window in wdata and "metrics" in wdata[window]:
                    dm = wdata[window]["metrics"]
                    pdf_table_row(pdf, [
                        strat_name, label,
                        f"{dm['total_return_pct']:+.1f}",
                        f"{dm['annualized_return_pct']:+.1f}",
                        f"{dm['sharpe_ratio']:.3f}",
                        f"{dm['max_drawdown_pct']:.1f}", "-",
                    ], col_widths)

        # Combined BTC+hedge rows
        if "combined" in all_results:
            for combo_name, wdata in all_results["combined"].items():
                if window in wdata and "metrics" in wdata[window]:
                    cm = wdata[window]["metrics"]
                    pdf_table_row(pdf, [
                        combo_name.replace("_", " "), label,
                        f"{cm['total_return_pct']:+.1f}",
                        f"{cm['annualized_return_pct']:+.1f}",
                        f"{cm['sharpe_ratio']:.3f}",
                        f"{cm['max_drawdown_pct']:.1f}", "-",
                    ], col_widths)

        # Standalone options rows
        if "options" in all_results:
            for strat_name, wdata in all_results["options"].items():
                if window in wdata:
                    om = wdata[window]["metrics"]
                    pdf_table_row(pdf, [
                        strat_name.replace("_", " "), label,
                        f"{om.total_return_pct:+.1f}", f"{om.annualized_return_pct:+.1f}",
                        f"{om.sharpe_ratio:.3f}", f"{om.max_drawdown_pct:.1f}",
                        str(om.num_rolls),
                    ], col_widths)


def _dca_section(pdf, all_results: Dict, time_windows: List[int]):
    """Section 2: BTC DCA results across windows."""
    pdf.add_page()
    pdf_section(pdf, "2. BTC DCA Results")
    pdf_body(pdf,
        "Pairwise DCA strategy applied to BTC (Grayscale Bitcoin Mini Trust ETF) "
        "across three time windows. The strategy places paired sell-stop / buy-limit "
        "orders to reduce cost basis through mean-reversion captures."
    )

    for window in time_windows:
        label = WINDOW_LABELS.get(window, f"{window}d")
        if "dca" not in all_results or window not in all_results["dca"]:
            continue

        m = all_results["dca"][window]["metrics"]
        pdf_subsection(pdf, f"BTC DCA -- {label} Window")

        stats = [
            f"Return: {m.total_return_pct:+.2f}% (Annualized: {m.annualized_return_pct:+.2f}%)",
            f"Sharpe Ratio: {m.sharpe_ratio:.3f}",
            f"Max Drawdown: {m.max_drawdown_pct:.2f}%",
            f"Pairs: {m.pairs_placed} placed, {m.pairs_completed} completed",
            f"Buy & Hold Return: {m.buy_hold_return_pct:+.2f}% (Excess: {m.excess_return_pct:+.2f}%)",
        ]
        for s in stats:
            pdf_bullet(pdf, s)

        chart_path = CHARTS_DIR / f"dca_equity_{label}.png"
        if chart_path.exists():
            pdf_embed_chart(pdf, chart_path)


def _benchmarks_section(pdf, all_results: Dict, time_windows: List[int]):
    """Section 3: Growth Benchmarks -- SPY/QQQ buy-hold vs BTC DCA."""
    if "benchmarks" not in all_results:
        return

    pdf.add_page()
    pdf_section(pdf, "3. Growth Benchmarks")
    pdf_body(pdf,
        "SPY (S&P 500) and QQQ (Nasdaq 100) buy-and-hold returns provide context "
        "for BTC DCA performance. All portfolios are normalized to the same initial "
        "capital for fair comparison. These benchmarks represent passive equity "
        "exposure without any active management or hedging."
    )

    for window in time_windows:
        label = WINDOW_LABELS.get(window, f"{window}d")
        pdf_subsection(pdf, f"Benchmarks -- {label} Window")

        # Show DCA return alongside benchmarks
        if "dca" in all_results and window in all_results["dca"]:
            dm = all_results["dca"][window]["metrics"]
            pdf_bullet(pdf, f"BTC DCA: {dm.total_return_pct:+.2f}%, Sharpe {dm.sharpe_ratio:.3f}")

        for bench_sym in ["SPY", "QQQ"]:
            if bench_sym in all_results["benchmarks"] and window in all_results["benchmarks"][bench_sym]:
                bm = all_results["benchmarks"][bench_sym][window]["metrics"]
                pdf_bullet(pdf,
                    f"{bench_sym} Buy & Hold: {bm['total_return_pct']:+.2f}%, "
                    f"Sharpe {bm['sharpe_ratio']:.3f}, MaxDD {bm['max_drawdown_pct']:.2f}%"
                )

        chart_path = CHARTS_DIR / f"benchmark_normalized_{label}.png"
        if chart_path.exists():
            pdf_embed_chart(pdf, chart_path)


def _drawdown_section(pdf, all_results: Dict, time_windows: List[int]):
    """Section 4: Drawdown Strategies -- cash reserve, trailing stop, adaptive sizing."""
    if "drawdown" not in all_results:
        return

    pdf.add_page()
    pdf_section(pdf, "4. Drawdown Strategies")
    pdf_body(pdf,
        "Three strategies designed for continued BTC drawdown scenarios. Each "
        "modifies the base DCA approach to limit losses during sustained declines."
    )

    desc_map = {
        "Cash Reserve": (
            "Pauses new DCA pair placement when the portfolio drops >10% from its "
            "rolling peak. Resumes when price recovers 5% from the trough. Existing "
            "orders continue to execute -- only new pair creation is halted."
        ),
        "Trailing Stop": (
            "Full position exit via an 8% trailing stop from the rolling price peak. "
            "Re-enters when price drops 5% below the exit price. This is a pure "
            "position-level strategy -- no DCA pairing."
        ),
        "Adaptive Sizing": (
            "Runs the DCA strategy but dynamically scales lot size inversely with "
            "rolling 21-day volatility. High vol = smaller lots (less whipsaw "
            "exposure), low vol = larger lots (capture more of the move)."
        ),
    }

    for strat_name, wdata in all_results["drawdown"].items():
        pdf_subsection(pdf, strat_name)
        if strat_name in desc_map:
            pdf_body(pdf, desc_map[strat_name])

        for window in time_windows:
            if window not in wdata:
                continue
            label = WINDOW_LABELS.get(window, f"{window}d")
            dm = wdata[window]["metrics"]
            dr = wdata[window]["result"]

            pdf.set_font("Helvetica", "BI", 10)
            pdf.cell(0, 7, safe_text(f"{label} Window"), new_x="LMARGIN", new_y="NEXT")

            stats = [
                f"Return: {dm['total_return_pct']:+.2f}%, Sharpe: {dm['sharpe_ratio']:.3f}, MaxDD: {dm['max_drawdown_pct']:.2f}%",
            ]
            # Add strategy-specific metadata
            meta = dr.metadata
            if strat_name == "Cash Reserve":
                stats.append(f"Pause count: {meta.get('pause_count', 0)}, "
                             f"Pairs: {meta.get('pairs_completed', 0)}/{meta.get('pairs_placed', 0)}")
            elif strat_name == "Trailing Stop":
                stats.append(f"Exits: {meta.get('exit_count', 0)}, "
                             f"Re-entries: {meta.get('reentry_count', 0)}")
            elif strat_name == "Adaptive Sizing":
                stats.append(f"Avg lot size: {meta.get('avg_adaptive_lot', 100):.0f}, "
                             f"Median vol: {meta.get('median_vol', 0):.2%}")

            for s in stats:
                pdf_bullet(pdf, s)

        # Chart per window
        for window in time_windows:
            label = WINDOW_LABELS.get(window, f"{window}d")
            chart_path = CHARTS_DIR / f"drawdown_strategies_{label}.png"
            if chart_path.exists():
                pdf_embed_chart(pdf, chart_path)
                break  # Only embed first window chart to avoid repetition


def _hedge_section(pdf, all_results: Dict, time_windows: List[int],
                   strategy_key: str, title: str):
    """Section for a combined BTC DCA + hedge strategy."""
    combo_key = f"BTC_DCA+{strategy_key}"
    if "combined" not in all_results or combo_key not in all_results["combined"]:
        return

    pdf.add_page()
    pdf_section(pdf, title)

    desc_map = {
        "SPY_PUT": (
            "This section shows BTC DCA portfolio combined with SPY protective puts. "
            "The put hedge provides downside protection on the broad market, partially "
            "offsetting BTC declines correlated with equity sell-offs."
        ),
        "IWM_PUT": (
            "This section shows BTC DCA portfolio combined with IWM protective puts. "
            "IWM (Russell 2000) tends to be more volatile than SPY, potentially "
            "providing stronger hedging signals during risk-off episodes."
        ),
        "SPY_COLLAR": (
            "This section shows BTC DCA portfolio combined with SPY collars "
            "(long put + short call). The collar reduces net hedging cost by "
            "selling upside via the call, but caps the combined portfolio's gains."
        ),
    }
    pdf_body(pdf, desc_map.get(strategy_key, ""))

    combo_data = all_results["combined"][combo_key]
    for window in time_windows:
        if window not in combo_data:
            continue
        label = WINDOW_LABELS.get(window, f"{window}d")
        pdf_subsection(pdf, f"{label} Window")

        cm = combo_data[window].get("metrics", {})
        if cm:
            stats = [
                f"Combined Return: {cm['total_return_pct']:+.2f}%",
                f"Sharpe Ratio: {cm['sharpe_ratio']:.3f}",
                f"Max Drawdown: {cm['max_drawdown_pct']:.2f}%",
            ]
            for s in stats:
                pdf_bullet(pdf, s)

        safe_name = combo_key.replace("+", "_plus_")
        chart_path = CHARTS_DIR / f"combined_{safe_name}_{label}.png"
        if chart_path.exists():
            pdf_embed_chart(pdf, chart_path)


def _standalone_options_section(pdf, all_results: Dict, time_windows: List[int]):
    """Section 8: Standalone options strategy results."""
    if "options" not in all_results:
        return

    pdf.add_page()
    pdf_section(pdf, "8. Standalone Options Strategies")
    pdf_body(pdf,
        "The following results show each options strategy in isolation -- "
        "holding the underlying shares with the specified hedge overlay, "
        "without any DCA component."
    )

    for strat_name, windows_data in all_results["options"].items():
        pdf_subsection(pdf, strat_name.replace("_", " "))

        for window in time_windows:
            if window not in windows_data:
                continue
            label = WINDOW_LABELS.get(window, f"{window}d")
            om = windows_data[window]["metrics"]

            pdf.set_font("Helvetica", "BI", 10)
            pdf.cell(0, 7, safe_text(f"{label} Window"), new_x="LMARGIN", new_y="NEXT")

            stats = [
                f"Return: {om.total_return_pct:+.2f}% | Sharpe: {om.sharpe_ratio:.3f} | Max DD: {om.max_drawdown_pct:.2f}%",
                f"Premium Paid: ${om.total_premium_paid:,.2f} | Received: ${om.total_premium_received:,.2f} | Net Cost: ${om.net_premium_cost:,.2f}",
                f"Put Intrinsic Recovered: ${om.total_intrinsic_recovered:,.2f} | Rolls: {om.num_rolls}",
            ]
            for s in stats:
                pdf_bullet(pdf, s)

            chart_path = CHARTS_DIR / f"options_{strat_name}_{label}.png"
            if chart_path.exists():
                pdf_embed_chart(pdf, chart_path)


def _options_cost_section(pdf, all_results: Dict, time_windows: List[int]):
    """Section 9: Options cost analysis table."""
    if "options" not in all_results:
        return

    pdf.add_page()
    pdf_section(pdf, "9. Options Cost Analysis")
    pdf_body(pdf,
        "Breakdown of options hedging costs: premiums paid for puts, "
        "premiums received from short calls (collar only), intrinsic value "
        "recovered at option expiry, and net protection cost as a percentage "
        "of initial portfolio value."
    )

    col_widths = [30, 16, 28, 28, 28, 28, 20]
    headers = ["Strategy", "Window", "Prem Paid", "Prem Rcvd", "Intrinsic", "Net Cost", "Cost%"]
    pdf_table_header(pdf, headers, col_widths)

    for strat_name, windows_data in all_results["options"].items():
        for window in time_windows:
            if window not in windows_data:
                continue
            label = WINDOW_LABELS.get(window, f"{window}d")
            om = windows_data[window]["metrics"]
            pdf_table_row(pdf, [
                strat_name.replace("_", " "), label,
                f"${om.total_premium_paid:,.0f}",
                f"${om.total_premium_received:,.0f}",
                f"${om.total_intrinsic_recovered:,.0f}",
                f"${om.net_premium_cost:,.0f}",
                f"{om.protection_cost_pct:.1f}%",
            ], col_widths)

    # Waterfall charts
    for window in time_windows:
        label = WINDOW_LABELS.get(window, f"{window}d")
        chart_path = CHARTS_DIR / f"options_waterfall_{label}.png"
        if chart_path.exists():
            pdf_embed_chart(pdf, chart_path)


def _risk_analysis_section(pdf):
    """Section 10: Risk Analysis."""
    pdf.add_page()
    pdf_section(pdf, "10. Risk Analysis")
    pdf_body(pdf,
        "This section identifies key risks that affect the BTC DCA strategy "
        "in real trading but are not captured by the backtest simulation."
    )

    pdf_subsection(pdf, "Weekend Gap Risk")
    pdf_body(pdf,
        "BTC ETF (NYSE: BTC) only trades Monday-Friday 9:30am-4:00pm ET. "
        "Crypto markets trade 24/7. A 10% weekend crypto drop means Monday's "
        "open gaps past the stop price -- the sell fills at the gap-down open, "
        "which can be significantly worse than the intended stop level. "
        "The backtest uses daily bars and does not model this gap risk."
    )

    pdf_subsection(pdf, "Flash Crash / Intraday Gap Risk")
    pdf_body(pdf,
        "Daily bars compress all intraday price action into a single candle. "
        "A flash crash that recovers within the same day may trigger both the "
        "sell stop AND the buy limit in the same bar -- overstating pair "
        "completion rates. In reality, the crash and recovery may happen too "
        "fast for orders to fill at intended prices."
    )

    pdf_subsection(pdf, "SELL_ONLY Cascade")
    pdf_body(pdf,
        "In sustained drawdowns, sell stops keep triggering but buy limits "
        "never fill (price doesn't drop far enough). The position shrinks as "
        "shares are sold, and cash accumulates. When the market eventually "
        "recovers, you hold fewer shares and miss the bounce. This is the "
        "DCA strategy's primary structural weakness -- it can progressively "
        "de-risk precisely when you should be adding exposure."
    )

    pdf_subsection(pdf, "Correlation Breakdown")
    pdf_body(pdf,
        "SPY/IWM hedges assume BTC price movements correlate with equities "
        "during stress periods. In crypto-specific sell-offs (regulation, "
        "exchange collapse, stablecoin de-peg), correlation with equities "
        "drops to near zero and the equity-based hedges provide no protection. "
        "The backtest cannot model regime-dependent correlations."
    )

    pdf_subsection(pdf, "Liquidity and Bid-Ask Spread")
    pdf_body(pdf,
        "BTC ETF spreads widen significantly during high volatility. Real "
        "fills will be worse than the simulation assumes (which uses exact "
        "stop/limit prices). During extreme moves, market orders may fill "
        "several cents away from the intended price, compounding losses "
        "across many paired orders."
    )

    pdf_subsection(pdf, "Tax Basis Complexity")
    pdf_body(pdf,
        "Every completed pair (sell + buy-back) is a taxable event. High "
        "completion rates mean a high volume of short-term capital gains/losses "
        "to report. Gains are always taxable -- the wash sale rule does NOT "
        "defer gains. However, the wash sale rule DOES apply to losses: if "
        "you sell at a loss and buy back the same security within 30 days, "
        "the loss is disallowed and added to the cost basis of the replacement "
        "shares. Since DCA pairs buy back immediately, virtually every losing "
        "pair triggers a wash sale, deferring the tax benefit of those losses."
    )


def _backtest_limitations_section(pdf):
    """Section 11: Backtest Limitations."""
    pdf.add_page()
    pdf_section(pdf, "11. Backtest Limitations")
    pdf_body(pdf,
        "Honest assessment of simulation flaws that may cause backtest results "
        "to diverge from real trading performance."
    )

    limitations = [
        ("Daily bar resolution", "Hides intraday dynamics. Stop orders "
         "that trigger and recover within a single bar are not modeled accurately."),
        ("No weekend/holiday gap modeling", "Critical for BTC ETF. Weekend crypto "
         "moves cause Monday open gaps that can blow past stop levels."),
        ("No slippage or bid-ask spread", "All fills occur at exact stop/limit "
         "prices. Real fills will be worse, especially during volatile periods."),
        ("Same-bar sell+buy triggers", "The simulation allows both legs of a pair "
         "to fill on the same daily bar, which overstates completion rates."),
        ("Fixed IV assumption (20%)", "Real implied volatility fluctuates between "
         "15-60% for BTC ETF options. Constant IV understates option costs during "
         "high-vol periods and overstates them during calm markets."),
        ("No transaction costs", "Commissions, exchange fees, and regulatory fees "
         "are not included. While small per-trade, they accumulate across many pairs."),
        ("Limited data history", "BTC ETF has ~389 trading days of history (~1.5 years). "
         "This is insufficient for statistically robust conclusions about strategy "
         "performance across multiple market cycles."),
        ("Survival bias", "We are only testing assets that still exist and trade today. "
         "Failed ETFs, delisted securities, and collapsed exchanges are not included."),
        ("No overnight gap risk", "The simulation does not model the risk of overnight "
         "gaps in the underlying security that could affect pending orders."),
    ]

    for title, desc in limitations:
        pdf_bullet(pdf, f"{title}: {desc}")


def _parameter_optimization_section(pdf, all_results: Dict):
    """Section 12: Parameter Optimization."""
    gs = all_results.get("grid_search")
    if not gs:
        return

    pdf.add_page()
    pdf_section(pdf, "12. Parameter Optimization")
    pdf_body(pdf,
        "Grid search over stop_offset_pct x buy_offset x coverage_threshold "
        "to identify parameter combinations that maximize the Sharpe ratio. "
        f"Total combinations tested: {len(gs.rows)}."
    )

    # Top 5 results table
    pdf_subsection(pdf, "Top 5 Parameter Combinations by Sharpe")
    col_widths = [22, 22, 22, 22, 22, 22, 22]
    headers = ["Stop%", "Buy$", "Cov%", "Sharpe", "Return%", "MaxDD%", "Comp%"]
    pdf_table_header(pdf, headers, col_widths)

    for row in gs.rows[:5]:
        pdf_table_row(pdf, [
            f"{row['stop_offset_pct']:.4f}",
            f"${row['buy_offset']:.2f}",
            f"{row['coverage_threshold']:.0%}",
            f"{row['sharpe_ratio']:.3f}",
            f"{row['total_return_pct']:+.1f}",
            f"{row['max_drawdown_pct']:.1f}",
            f"{row['completion_rate_pct']:.0f}",
        ], col_widths)

    # Heatmap
    chart_path = CHARTS_DIR / "grid_search_heatmap.png"
    if chart_path.exists():
        pdf_embed_chart(pdf, chart_path)

    # Regime suggestions
    regime = all_results.get("regime_suggestions")
    if regime:
        pdf_subsection(pdf, "Regime-Based Parameter Suggestions")
        pdf_body(pdf, f"Current regime: {regime['regime']} "
                      f"(rolling vol = {regime['rolling_vol_pct']:.1f}%)")
        pdf_bullet(pdf, f"Suggested stop_offset: {regime['stop_offset_pct']:.4f}")
        pdf_bullet(pdf, f"Suggested buy_offset: ${regime['buy_offset']:.2f}")
        pdf_bullet(pdf, f"Suggested coverage: {regime['coverage_threshold']:.0%}")
        pdf_bullet(pdf, f"Rationale: {regime['rationale']}")

        # Regime rules table
        pdf_subsection(pdf, "Regime Rules Summary")
        col_widths = [28, 28, 28, 28, 50]
        headers = ["Regime", "Stop%", "Buy$", "Cov%", "Condition"]
        pdf_table_header(pdf, headers, col_widths)
        pdf_table_row(pdf, ["LOW_VOL", "0.0075", "$0.15", "25%", "Ann. vol < 15%"], col_widths)
        pdf_table_row(pdf, ["MEDIUM_VOL", "0.0100", "$0.20", "20%", "15% <= vol <= 30%"], col_widths)
        pdf_table_row(pdf, ["HIGH_VOL", "0.0200", "$0.35", "15%", "Ann. vol > 30%"], col_widths)


def _drift_detection_section(pdf, all_results: Dict):
    """Section 13: Parameter Monitoring & Drift Detection."""
    rolling = all_results.get("rolling_metrics")
    drift = all_results.get("drift_alerts")
    if not rolling:
        return

    pdf.add_page()
    pdf_section(pdf, "13. Parameter Monitoring & Drift Detection")
    pdf_body(pdf,
        "Rolling 21-day metrics track strategy health over time. Drift alerts "
        "flag dates when metrics cross warning thresholds, indicating the "
        "strategy may be underperforming and parameters should be reviewed."
    )

    # Rolling Sharpe chart
    chart_path = CHARTS_DIR / "rolling_sharpe.png"
    if chart_path.exists():
        pdf_embed_chart(pdf, chart_path)

    # Rolling completion chart
    chart_path = CHARTS_DIR / "rolling_completion.png"
    if chart_path.exists():
        pdf_embed_chart(pdf, chart_path)

    # Rolling vol regimes chart
    chart_path = CHARTS_DIR / "rolling_vol_regimes.png"
    if chart_path.exists():
        pdf_embed_chart(pdf, chart_path)

    # Drift alerts table
    if drift:
        pdf_subsection(pdf, f"Drift Alerts ({len(drift)} total)")
        col_widths = [35, 45, 35, 35]
        headers = ["Date", "Metric", "Value", "Threshold"]
        pdf_table_header(pdf, headers, col_widths)

        # Show first 20 alerts to avoid excessively long tables
        for alert in drift[:20]:
            pdf_table_row(pdf, [
                alert["date"],
                alert["metric"].replace("_", " "),
                f"{alert['value']:.2f}",
                f"{alert['threshold']:.2f}",
            ], col_widths)

        if len(drift) > 20:
            pdf_body(pdf, f"... and {len(drift) - 20} more alerts (truncated).")
    else:
        pdf_body(pdf, "No drift alerts detected -- all rolling metrics within thresholds.")

    # Monitoring recommendations
    pdf_subsection(pdf, "Recommended Monitoring Rules")
    rules = [
        "Review parameters when rolling 21-day Sharpe drops below -1.0 for 3+ consecutive days",
        "Investigate if rolling coverage rate falls below 50% -- may indicate SELL_ONLY cascade",
        "Switch to HIGH_VOL regime parameters when annualized volatility exceeds 30%",
        "Switch to LOW_VOL regime parameters when annualized volatility drops below 15%",
        "Run grid search monthly on latest 126-day window to check for parameter drift",
        "Consider pausing the strategy entirely if max drawdown exceeds 15% in any 21-day window",
    ]
    for r in rules:
        pdf_bullet(pdf, r)


def _methodology_section(pdf, all_results: Dict):
    """Section 14: Methodology."""
    pdf.add_page()
    pdf_section(pdf, "14. Methodology")

    pdf_subsection(pdf, "Black-Scholes Assumptions")
    bullets = [
        "European-style options pricing (Black-Scholes model)",
        "Constant implied volatility (default 20% annualized)",
        "Risk-free rate: 5% annualized",
        "No transaction costs or bid-ask spread on options",
        "Monthly rolling: options expire every 21 trading days and are re-struck",
    ]
    for b in bullets:
        pdf_bullet(pdf, b)

    pdf_subsection(pdf, "Strike Selection")
    bullets = [
        "Put strike: underlying close * (1 - OTM%), default 5% OTM",
        "Call strike (collar): underlying close * (1 + OTM%), default 5% OTM",
        "Strikes re-set at each roll to current market price",
    ]
    for b in bullets:
        pdf_bullet(pdf, b)

    pdf_subsection(pdf, "Portfolio Construction")
    bullets = [
        "BTC DCA: Pairwise sell-stop / buy-limit strategy on BTC ETF shares",
        "Options hedge: 100 shares of SPY or IWM with monthly put or collar overlay",
        "Combined portfolio: BTC DCA net_value + options strategy net P&L",
        "The hedge is funded from a separate capital allocation",
        "No delta hedging or dynamic adjustment between roll dates",
    ]
    for b in bullets:
        pdf_bullet(pdf, b)

    pdf_subsection(pdf, "Drawdown Strategies")
    bullets = [
        "Cash Reserve: Pauses DCA pairing when portfolio drops >10% from peak; resumes on 5% recovery",
        "Trailing Stop: Full exit at 8% trailing stop from peak; re-entry at 5% below exit price",
        "Adaptive Sizing: Scales lot size inversely with 21-day rolling volatility (0.25x-2.0x range)",
    ]
    for b in bullets:
        pdf_bullet(pdf, b)

    pdf_subsection(pdf, "Parameter Optimization")
    bullets = [
        "Grid search: 5 stop_offsets x 5 buy_offsets x 3 coverages = 75 parameter combos",
        "Objective: maximize annualized Sharpe ratio",
        "Rolling metrics: 21-day rolling window for Sharpe, completion rate, and volatility",
        "Drift detection: alerts when rolling Sharpe < -1.0 or coverage < 50%",
        "Regime suggestions: parameter adjustments based on volatility bands (low/medium/high)",
    ]
    for b in bullets:
        pdf_bullet(pdf, b)

    pdf_subsection(pdf, "Normal CDF Approximation")
    pdf_body(pdf,
        "To avoid a scipy dependency, the Black-Scholes implementation uses "
        "the Abramowitz & Stegun rational approximation (formula 7.1.26) for "
        "the standard normal CDF, with maximum error of approximately 1.5e-7."
    )


def _conclusion_section(pdf, all_results: Dict, time_windows: List[int]):
    """Section 15: Conclusion."""
    pdf_section(pdf, "15. Conclusion")

    pdf_body(pdf,
        "This analysis compared BTC DCA performance with and without "
        "options-based hedging overlays, drawdown-protective strategies, "
        "and growth benchmarks across multiple time windows. "
        "Key findings:"
    )

    # Dynamic findings based on results
    if "dca" in all_results:
        for window in time_windows:
            label = WINDOW_LABELS.get(window, f"{window}d")
            if window not in all_results["dca"]:
                continue
            dca_ret = all_results["dca"][window]["metrics"].total_return_pct

            findings = [f"{label}: BTC DCA returned {dca_ret:+.1f}%"]

            # Benchmarks
            if "benchmarks" in all_results:
                for bench_sym in ["SPY", "QQQ"]:
                    if bench_sym in all_results["benchmarks"] and window in all_results["benchmarks"][bench_sym]:
                        bm = all_results["benchmarks"][bench_sym][window]["metrics"]
                        delta = dca_ret - bm["total_return_pct"]
                        findings.append(
                            f"  vs {bench_sym}: {bm['total_return_pct']:+.1f}% "
                            f"(BTC DCA {delta:+.1f}% relative)"
                        )

            # Combined hedges
            if "combined" in all_results:
                for combo_name, wdata in all_results["combined"].items():
                    if window in wdata and "metrics" in wdata[window]:
                        cm = wdata[window]["metrics"]
                        delta = cm["total_return_pct"] - dca_ret
                        findings.append(
                            f"  {combo_name.replace('_', ' ')}: {cm['total_return_pct']:+.1f}% "
                            f"({delta:+.1f}% vs unhedged)"
                        )

            for f in findings:
                pdf_bullet(pdf, f)

    # Drawdown strategy findings
    if "drawdown" in all_results:
        pdf_body(pdf, "Drawdown strategy results:")
        for strat_name, wdata in all_results["drawdown"].items():
            if time_windows[0] in wdata:
                dm = wdata[time_windows[0]]["metrics"]
                label = WINDOW_LABELS.get(time_windows[0], f"{time_windows[0]}d")
                pdf_bullet(pdf,
                    f"{strat_name} ({label}): {dm['total_return_pct']:+.1f}%, "
                    f"Sharpe {dm['sharpe_ratio']:.3f}"
                )

    # Grid search findings
    gs = all_results.get("grid_search")
    if gs:
        pdf_body(pdf,
            f"Parameter optimization identified optimal parameters: "
            f"stop_offset={gs.best['stop_offset_pct']:.4f}, "
            f"buy_offset=${gs.best['buy_offset']:.2f}, "
            f"coverage={gs.best['coverage_threshold']:.0%} "
            f"(Sharpe {gs.best['sharpe_ratio']:.3f})."
        )

    pdf_body(pdf,
        "Options hedging provides downside protection but at a cost. "
        "Protective puts reduce max drawdown during market declines but "
        "create a premium drag during calm markets. Collars offset this "
        "cost by selling upside, making them more efficient hedges when "
        "the underlying trades sideways."
    )

    pdf_body(pdf,
        "The drawdown strategies offer alternatives to hedging: the Cash Reserve "
        "strategy avoids committing new capital during drawdowns, while the "
        "Trailing Stop provides a hard exit mechanism. The Adaptive Sizing "
        "strategy dynamically adjusts exposure based on market conditions."
    )

    pdf_body(pdf,
        "IMPORTANT: These results should be interpreted with caution given the "
        "significant backtest limitations documented in Section 11. Weekend gap "
        "risk, slippage, and the limited data history all suggest real-world "
        "performance will likely be worse than simulated results."
    )
