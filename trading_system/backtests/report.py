"""
Report generator — produces PNG charts, a Markdown paper, and a PDF
from backtest results.
"""

import os
import textwrap
from pathlib import Path
from typing import Dict, List, Optional

from trading_system.config import DEFAULT_LOT_SIZE
from trading_system.backtests.metrics import (
    BacktestMetrics,
    buy_hold_comparison,
)
from trading_system.backtests.pdf_helpers import (
    PaperPDF as _PaperPDFBase,
    safe_text as _safe,
    pdf_section as _section,
    pdf_subsection as _subsection,
    pdf_body as _body,
    pdf_bullet as _bullet,
    pdf_table_header as _pdf_table_header,
    pdf_table_row as _pdf_table_row,
    pdf_embed_chart as _embed_chart,
)
from trading_system.backtests.simulation import PairState, SimulationResult

DOCS_DIR = Path(__file__).parent.parent.parent / "docs"
CHARTS_DIR = DOCS_DIR / "charts"


def generate_report(
    sim_results: Dict[str, SimulationResult],
    metrics: Dict[str, BacktestMetrics],
    sensitivity_results: Optional[Dict[str, Dict[str, List[Dict]]]] = None,
):
    """
    Generate charts and write the Markdown paper.

    Args:
        sim_results: symbol -> SimulationResult
        metrics: symbol -> BacktestMetrics
        sensitivity_results: symbol -> {param_name -> [{param_value, ...metrics}]}
    """
    CHARTS_DIR.mkdir(parents=True, exist_ok=True)

    # Generate charts per symbol
    for symbol, result in sim_results.items():
        m = metrics[symbol]
        _plot_equity_curves(symbol, result, m)
        _plot_drawdown(symbol, result)
        _plot_cost_basis(symbol, result)
        _plot_pair_activity(symbol, result)

    # Sensitivity charts
    if sensitivity_results:
        for symbol, params in sensitivity_results.items():
            for param_name, data in params.items():
                _plot_sensitivity(symbol, param_name, data)

    # Write the paper
    _write_paper(sim_results, metrics, sensitivity_results)
    print(f"\nReport written to {DOCS_DIR / 'pairwise_dca_strategy.md'}")
    print(f"Charts saved to {CHARTS_DIR}/")


def _plot_equity_curves(symbol: str, result: SimulationResult, m: BacktestMetrics):
    import matplotlib.pyplot as plt
    import matplotlib.dates as mdates
    from datetime import datetime

    comparison = buy_hold_comparison(result)
    dates = [datetime.strptime(r["date"], "%Y-%m-%d") for r in comparison]
    dca_vals = [r["dca_value"] for r in comparison]
    bh_vals = [r["buy_hold_value"] for r in comparison]

    fig, ax = plt.subplots(figsize=(12, 6))
    ax.plot(dates, dca_vals, label=f"Pairwise DCA ({m.total_return_pct:+.1f}%)", linewidth=1.5)
    ax.plot(dates, bh_vals, label=f"Buy & Hold ({m.buy_hold_return_pct:+.1f}%)",
            linewidth=1.5, linestyle="--", alpha=0.8)
    ax.set_title(f"{symbol} — Equity Curves: Pairwise DCA vs Buy & Hold")
    ax.set_ylabel("Portfolio Value ($)")
    ax.legend()
    ax.grid(True, alpha=0.3)
    ax.xaxis.set_major_formatter(mdates.DateFormatter("%Y-%m"))
    ax.xaxis.set_major_locator(mdates.MonthLocator(interval=3))
    fig.autofmt_xdate()
    fig.tight_layout()
    fig.savefig(CHARTS_DIR / f"{symbol}_equity_curves.png", dpi=150)
    plt.close(fig)


def _plot_drawdown(symbol: str, result: SimulationResult):
    import matplotlib.pyplot as plt
    import matplotlib.dates as mdates
    from datetime import datetime

    snapshots = result.snapshots
    dates = [datetime.strptime(s.date, "%Y-%m-%d") for s in snapshots]
    values = [s.net_value for s in snapshots]

    # Compute running drawdown
    peak = values[0]
    drawdowns = []
    for v in values:
        if v > peak:
            peak = v
        dd = (peak - v) / peak * 100 if peak > 0 else 0
        drawdowns.append(-dd)

    fig, ax = plt.subplots(figsize=(12, 4))
    ax.fill_between(dates, drawdowns, 0, alpha=0.4, color="red")
    ax.plot(dates, drawdowns, color="red", linewidth=0.8)
    ax.set_title(f"{symbol} — Drawdown (%)")
    ax.set_ylabel("Drawdown (%)")
    ax.grid(True, alpha=0.3)
    ax.xaxis.set_major_formatter(mdates.DateFormatter("%Y-%m"))
    ax.xaxis.set_major_locator(mdates.MonthLocator(interval=3))
    fig.autofmt_xdate()
    fig.tight_layout()
    fig.savefig(CHARTS_DIR / f"{symbol}_drawdown.png", dpi=150)
    plt.close(fig)


def _plot_cost_basis(symbol: str, result: SimulationResult):
    import matplotlib.pyplot as plt
    import matplotlib.dates as mdates
    from datetime import datetime

    snapshots = result.snapshots
    dates = [datetime.strptime(s.date, "%Y-%m-%d") for s in snapshots]
    prices = [s.price for s in snapshots]
    cost_bases = [s.cost_basis for s in snapshots]

    fig, ax = plt.subplots(figsize=(12, 5))
    ax.plot(dates, prices, label="Market Price", linewidth=1.2, alpha=0.7)
    ax.plot(dates, cost_bases, label="Cost Basis", linewidth=1.5, color="green")
    ax.set_title(f"{symbol} — Cost Basis Evolution")
    ax.set_ylabel("Price ($)")
    ax.legend()
    ax.grid(True, alpha=0.3)
    ax.xaxis.set_major_formatter(mdates.DateFormatter("%Y-%m"))
    ax.xaxis.set_major_locator(mdates.MonthLocator(interval=3))
    fig.autofmt_xdate()
    fig.tight_layout()
    fig.savefig(CHARTS_DIR / f"{symbol}_cost_basis.png", dpi=150)
    plt.close(fig)


def _plot_pair_activity(symbol: str, result: SimulationResult):
    import matplotlib.pyplot as plt

    pairs = result.pairs
    states = [PairState.COMPLETED, PairState.SELL_ONLY, PairState.CANCELLED, PairState.PENDING]
    labels = ["Completed", "Sell Only", "Cancelled", "Pending"]
    counts = [sum(1 for p in pairs if p.state == s) for s in states]
    colors = ["#2ecc71", "#e67e22", "#e74c3c", "#95a5a6"]

    # Filter out zero counts
    filtered = [(l, c, col) for l, c, col in zip(labels, counts, colors) if c > 0]
    if not filtered:
        return

    fig, ax = plt.subplots(figsize=(8, 5))
    ax.bar([f[0] for f in filtered], [f[1] for f in filtered],
           color=[f[2] for f in filtered])
    ax.set_title(f"{symbol} — Paired Order Outcomes")
    ax.set_ylabel("Count")
    for i, (label, count, _) in enumerate(filtered):
        ax.text(i, count + 0.5, str(count), ha="center", fontweight="bold")
    fig.tight_layout()
    fig.savefig(CHARTS_DIR / f"{symbol}_pair_activity.png", dpi=150)
    plt.close(fig)


def _plot_sensitivity(symbol: str, param_name: str, data: List[Dict]):
    import matplotlib.pyplot as plt

    if not data:
        return

    values = [d["param_value"] for d in data]
    returns = [d["total_return_pct"] for d in data]
    sharpes = [d["sharpe_ratio"] for d in data]

    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(14, 5))

    # Format x labels
    if param_name == "stop_offset_pct":
        x_labels = [f"{v*100:.1f}%" for v in values]
        title_param = "Stop Offset %"
    elif param_name == "buy_offset":
        x_labels = [f"${v:.2f}" for v in values]
        title_param = "Buy Offset ($)"
    elif param_name == "coverage_threshold":
        x_labels = [f"{v*100:.0f}%" for v in values]
        title_param = "Coverage Threshold"
    else:
        x_labels = [str(v) for v in values]
        title_param = param_name

    ax1.bar(x_labels, returns, color="#3498db")
    ax1.set_title(f"{symbol} — Total Return vs {title_param}")
    ax1.set_ylabel("Total Return (%)")
    ax1.set_xlabel(title_param)

    ax2.bar(x_labels, sharpes, color="#2ecc71")
    ax2.set_title(f"{symbol} — Sharpe Ratio vs {title_param}")
    ax2.set_ylabel("Sharpe Ratio")
    ax2.set_xlabel(title_param)

    fig.tight_layout()
    fig.savefig(CHARTS_DIR / f"{symbol}_sensitivity_{param_name}.png", dpi=150)
    plt.close(fig)


def _write_paper(
    sim_results: Dict[str, SimulationResult],
    metrics: Dict[str, BacktestMetrics],
    sensitivity_results: Optional[Dict[str, Dict[str, List[Dict]]]] = None,
):
    """Write the Markdown paper with embedded chart references."""
    symbols = list(sim_results.keys())
    lines = []

    # ── 1. Title & Executive Summary ─────────────────────────────────────
    lines.append("# Pairwise DCA Strategy: Backtest Analysis")
    lines.append("")
    lines.append("## 1. Executive Summary")
    lines.append("")
    lines.append("This paper presents the backtest results of the **Pairwise Dollar-Cost Averaging (DCA)**")
    lines.append("strategy as implemented in the `MomentumDcaLongStrategy`. The strategy maintains")
    lines.append("protective sell-stop orders on held positions, paired with limit-buy orders to")
    lines.append("re-enter at a lower price, effectively reducing cost basis over time.")
    lines.append("")

    # Summary table
    lines.append("| Symbol | DCA Return | Buy & Hold Return | Excess Return | Sharpe | Max Drawdown |")
    lines.append("|--------|-----------|-------------------|---------------|--------|-------------|")
    for sym in symbols:
        m = metrics[sym]
        lines.append(
            f"| {sym} | {m.total_return_pct:+.2f}% | {m.buy_hold_return_pct:+.2f}% | "
            f"{m.excess_return_pct:+.2f}% | {m.sharpe_ratio:.3f} | {m.max_drawdown_pct:.2f}% |"
        )
    lines.append("")

    # ── 2. Strategy Overview ─────────────────────────────────────────────
    lines.append("## 2. Strategy Overview")
    lines.append("")
    lines.append("The Pairwise DCA strategy works by continuously maintaining sell-stop orders")
    lines.append("on a fraction of the held position. When the market drops and triggers a sell,")
    lines.append("a paired limit-buy order is already in place to re-acquire the shares at a lower")
    lines.append("price, capturing the spread as a cost basis improvement.")
    lines.append("")
    lines.append("### Parameters")
    lines.append("")
    lines.append("| Parameter | Default | Description |")
    lines.append("|-----------|---------|-------------|")
    lines.append("| `stop_offset_pct` | 1.0% | Sell stop placed at `open * (1 - offset)` |")
    lines.append("| `buy_offset` | $0.20 | Buy limit placed at `stop_price - $0.20` |")
    lines.append("| `coverage_threshold` | 20% | Minimum fraction of position covered by pending pairs |")
    lines.append("| `proximity_pct` | 0.75% | Skip new pair if within this % of existing pair |")
    lines.append(f"| `lot_size` | {DEFAULT_LOT_SIZE} | Maximum shares per paired order |")
    lines.append("| `sell_expiry_days` | 30 | Trading days before unfilled sell is cancelled |")
    lines.append("| `buy_expiry_days` | 30 | Trading days before unfilled buy expires |")
    lines.append("")
    lines.append("### Order Lifecycle")
    lines.append("")
    lines.append("```")
    lines.append("PENDING ──(bar.low ≤ stop_price)──> SELL_TRIGGERED ──(bar.low ≤ buy_price)──> COMPLETED")
    lines.append("   │                                        │")
    lines.append("   │ (30 days, no trigger)                  │ (30 days, no buy fill)")
    lines.append("   v                                        v")
    lines.append("CANCELLED                              SELL_ONLY")
    lines.append("```")
    lines.append("")

    # ── 3. Backtest Methodology ──────────────────────────────────────────
    lines.append("## 3. Backtest Methodology")
    lines.append("")
    lines.append("- **Data Source**: Twelve Data API, daily OHLCV bars")
    lines.append("- **Resolution**: Daily bars; stops trigger if `bar.low <= stop_price`")
    lines.append("- **Fill Assumption**: Orders fill at limit price (no slippage), consistent")
    lines.append("  with Robinhood's commission-free execution model")
    lines.append("- **Initial Positions**: Matched to live portfolio holdings at acquisition cost")
    lines.append("- **Rebalancing**: None; the strategy only rotates within existing positions")
    lines.append("")

    # ── 4. Results ───────────────────────────────────────────────────────
    lines.append("## 4. Results")
    lines.append("")
    for sym in symbols:
        m = metrics[sym]
        lines.append(f"### {sym}")
        lines.append("")
        lines.append(f"- **Initial Value**: ${m.initial_value:,.2f}")
        lines.append(f"- **Final Value (DCA)**: ${m.final_net_value:,.2f}")
        lines.append(f"- **Final Value (Buy & Hold)**: ${m.buy_hold_final_value:,.2f}")
        lines.append(f"- **Total Return**: {m.total_return_pct:+.2f}% (vs B&H: {m.buy_hold_return_pct:+.2f}%)")
        lines.append(f"- **Annualized Return**: {m.annualized_return_pct:+.2f}%")
        lines.append(f"- **Sharpe Ratio**: {m.sharpe_ratio:.3f}")
        lines.append(f"- **Max Drawdown**: {m.max_drawdown_pct:.2f}% ({m.max_drawdown_start} to {m.max_drawdown_end})")
        lines.append("")
        lines.append(f"![{sym} Equity Curves](charts/{sym}_equity_curves.png)")
        lines.append("")
        lines.append(f"![{sym} Drawdown](charts/{sym}_drawdown.png)")
        lines.append("")
        lines.append(f"![{sym} Cost Basis](charts/{sym}_cost_basis.png)")
        lines.append("")

    # ── 5. Paired Order Statistics ───────────────────────────────────────
    lines.append("## 5. Paired Order Statistics")
    lines.append("")
    lines.append("| Symbol | Placed | Completed | Sell Only | Cancelled | Completion Rate | Avg Days | Avg Improvement |")
    lines.append("|--------|--------|-----------|-----------|-----------|-----------------|----------|-----------------|")
    for sym in symbols:
        m = metrics[sym]
        lines.append(
            f"| {sym} | {m.pairs_placed} | {m.pairs_completed} | {m.pairs_sell_only} | "
            f"{m.pairs_cancelled} | {m.completion_rate_pct:.1f}% | {m.avg_days_to_complete:.1f} | "
            f"{m.avg_cost_basis_improvement_pct:.4f}% |"
        )
    lines.append("")
    for sym in symbols:
        lines.append(f"![{sym} Pair Activity](charts/{sym}_pair_activity.png)")
        lines.append("")

    # ── 6. Parameter Sensitivity ─────────────────────────────────────────
    if sensitivity_results:
        lines.append("## 6. Parameter Sensitivity")
        lines.append("")
        lines.append("Each parameter was varied individually while holding others at default values.")
        lines.append("")

        param_descriptions = {
            "stop_offset_pct": "Stop Offset (0.5% - 3.0%)",
            "buy_offset": "Buy Offset ($0.10 - $0.50)",
            "coverage_threshold": "Coverage Threshold (10% - 30%)",
        }

        for sym, params in sensitivity_results.items():
            lines.append(f"### {sym}")
            lines.append("")
            for param_name, data in params.items():
                desc = param_descriptions.get(param_name, param_name)
                lines.append(f"#### {desc}")
                lines.append("")
                lines.append(f"![{sym} Sensitivity {param_name}](charts/{sym}_sensitivity_{param_name}.png)")
                lines.append("")

                # Data table
                lines.append("| Value | Return | Sharpe | Drawdown | Pairs | Completion |")
                lines.append("|-------|--------|--------|----------|-------|------------|")
                for d in data:
                    if param_name == "stop_offset_pct":
                        val_str = f"{d['param_value']*100:.1f}%"
                    elif param_name == "buy_offset":
                        val_str = f"${d['param_value']:.2f}"
                    elif param_name == "coverage_threshold":
                        val_str = f"{d['param_value']*100:.0f}%"
                    else:
                        val_str = str(d["param_value"])
                    lines.append(
                        f"| {val_str} | {d['total_return_pct']:+.2f}% | "
                        f"{d['sharpe_ratio']:.3f} | {d['max_drawdown_pct']:.2f}% | "
                        f"{d['pairs_placed']} | {d['completion_rate_pct']:.1f}% |"
                    )
                lines.append("")

    # ── 7. Risk Analysis ─────────────────────────────────────────────────
    lines.append("## 7. Risk Analysis")
    lines.append("")
    lines.append("### Drawdown Comparison")
    lines.append("")
    lines.append("The pairwise DCA strategy may experience slightly higher drawdowns than buy-and-hold")
    lines.append("during sustained declines, because sell stops trigger (reducing shares) but buy limits")
    lines.append("may not fill if the decline continues beyond the buy offset.")
    lines.append("")
    lines.append("### Sustained Decline Scenario")
    lines.append("")
    lines.append("In a sustained decline, the strategy repeatedly sells at stop prices but buy orders")
    lines.append("may expire unfilled (SELL_ONLY outcomes), resulting in gradual position reduction")
    lines.append("and accumulated cash. This acts as a natural de-risking mechanism but can lead to")
    lines.append("underperformance in a V-shaped recovery.")
    lines.append("")
    lines.append("### Sustained Rally Scenario")
    lines.append("")
    lines.append("In a sustained rally, sell stops are never triggered (all pairs remain PENDING then")
    lines.append("CANCELLED). The strategy matches buy-and-hold exactly, with no cost basis improvement")
    lines.append("opportunity. No harm done, but no benefit either.")
    lines.append("")
    lines.append("### Cash Drag")
    lines.append("")
    lines.append("When sell orders fill but buy orders have not yet filled, the strategy holds cash.")
    lines.append("This cash earns no return in the simulation (conservative assumption). In practice,")
    lines.append("Robinhood sweeps idle cash into money market funds, partially offsetting this drag.")
    lines.append("")
    lines.append("### Dollar-Amount Buy Offset")
    lines.append("")
    lines.append("The $0.20 fixed buy_offset is significant for low-priced securities like BTC")
    lines.append("(Grayscale Bitcoin Mini Trust ETF at ~$31, representing 0.65% spread) but")
    lines.append("negligible for SPY/QQQ (~$450, representing only 0.04%). The parameter sensitivity")
    lines.append("analysis above quantifies this asymmetry.")
    lines.append("")

    # ── 8. Limitations & Future Work ─────────────────────────────────────
    lines.append("## 8. Limitations & Future Work")
    lines.append("")
    lines.append("### Limitations")
    lines.append("")
    lines.append("1. **Daily resolution**: Intraday price action is compressed into OHLCV bars.")
    lines.append("   A stop and buy could both trigger in the same bar, which may overstate")
    lines.append("   completion rates vs real intraday execution.")
    lines.append("2. **No slippage model**: Fills assumed at exact limit prices. Real execution")
    lines.append("   may experience minor slippage, especially in volatile markets.")
    lines.append("3. **No partial fills**: Each paired order fills completely or not at all.")
    lines.append("4. **Fixed lot sizing**: The simulation uses a fixed lot_size cap rather than")
    lines.append("   the live system's dynamic position-proportional sizing.")
    lines.append("5. **No dividends or corporate actions**: SPY/QQQ dividends are not reinvested.")
    lines.append("")
    lines.append("### Future Work")
    lines.append("")
    lines.append("1. Percentage-based buy_offset (e.g., 0.5% of stop price) to normalize")
    lines.append("   across different price levels")
    lines.append("2. Intraday (5-min) bar resolution for more accurate fill simulation")
    lines.append("3. Monte Carlo analysis with randomized entry points")
    lines.append("4. Multi-asset correlation analysis (do all symbols benefit equally?)")
    lines.append("5. Transaction cost sensitivity (for non-Robinhood brokers)")
    lines.append("")

    # ── 9. Conclusion ────────────────────────────────────────────────────
    lines.append("## 9. Conclusion")
    lines.append("")

    # Dynamic conclusion based on results
    positive = [sym for sym in symbols if metrics[sym].excess_return_pct > 0]
    negative = [sym for sym in symbols if metrics[sym].excess_return_pct <= 0]

    if positive:
        lines.append(f"The Pairwise DCA strategy generated excess returns over buy-and-hold for")
        lines.append(f"**{', '.join(positive)}**, demonstrating that the paired sell-stop / buy-limit")
        lines.append(f"mechanism can capture mean-reversion opportunities in volatile markets.")
    if negative:
        lines.append(f"For **{', '.join(negative)}**, the strategy underperformed buy-and-hold,")
        lines.append(f"likely due to sustained directional moves that resulted in position reduction")
        lines.append(f"without corresponding re-entry opportunities.")
    lines.append("")
    lines.append("The strategy is most effective in range-bound or moderately volatile markets")
    lines.append("where prices oscillate within the stop-buy spread, allowing repeated cost basis")
    lines.append("improvements. It serves as a defensive overlay that naturally de-risks during")
    lines.append("sustained declines while matching buy-and-hold during rallies.")
    lines.append("")
    lines.append("---")
    lines.append("")
    lines.append("*Generated by `trading_system.backtests.pairwise_dca_backtest`*")
    lines.append("")

    paper_path = DOCS_DIR / "pairwise_dca_strategy.md"
    with open(paper_path, "w") as f:
        f.write("\n".join(lines))


# ── PDF Generation ───────────────────────────────────────────────────────


def generate_pdf(
    sim_results: Dict[str, SimulationResult],
    metrics: Dict[str, BacktestMetrics],
    sensitivity_results: Optional[Dict[str, Dict[str, List[Dict]]]] = None,
) -> Path:
    """
    Generate a formatted PDF paper with embedded charts.

    Returns the path to the generated PDF.
    """
    from fpdf import FPDF

    symbols = list(sim_results.keys())
    pdf_path = DOCS_DIR / "pairwise_dca_strategy.pdf"

    pdf = _PaperPDFBase(title="Pairwise DCA Strategy - Backtest Analysis")
    pdf.set_auto_page_break(auto=True, margin=20)

    # ── Title page ───────────────────────────────────────────────────────
    pdf.add_page()
    pdf.ln(40)
    pdf.set_font("Helvetica", "B", 28)
    pdf.cell(0, 15, "Pairwise DCA Strategy", new_x="LMARGIN", new_y="NEXT", align="C")
    pdf.set_font("Helvetica", "", 20)
    pdf.cell(0, 12, "Backtest Analysis", new_x="LMARGIN", new_y="NEXT", align="C")
    pdf.ln(15)
    pdf.set_font("Helvetica", "", 12)
    pdf.cell(0, 8, "MomentumDcaLongStrategy Performance Evaluation", new_x="LMARGIN", new_y="NEXT", align="C")
    pdf.ln(5)

    # Summary box on title page
    pdf.ln(10)
    pdf.set_font("Helvetica", "B", 11)
    pdf.cell(0, 8, "Summary", new_x="LMARGIN", new_y="NEXT")
    pdf.set_font("Helvetica", "", 10)

    col_widths = [22, 32, 38, 32, 22, 30]
    headers = ["Symbol", "DCA Return", "B&H Return", "Excess", "Sharpe", "Max DD"]
    _pdf_table_header(pdf, headers, col_widths)
    for sym in symbols:
        m = metrics[sym]
        row = [
            sym,
            f"{m.total_return_pct:+.2f}%",
            f"{m.buy_hold_return_pct:+.2f}%",
            f"{m.excess_return_pct:+.2f}%",
            f"{m.sharpe_ratio:.3f}",
            f"{m.max_drawdown_pct:.2f}%",
        ]
        _pdf_table_row(pdf, row, col_widths)

    # ── 1. Executive Summary ─────────────────────────────────────────────
    pdf.add_page()
    _section(pdf, "1. Executive Summary")
    _body(pdf,
        "This paper presents the backtest results of the Pairwise Dollar-Cost "
        "Averaging (DCA) strategy as implemented in the MomentumDcaLongStrategy. "
        "The strategy maintains protective sell-stop orders on held positions, "
        "paired with limit-buy orders to re-enter at a lower price, effectively "
        "reducing cost basis over time."
    )
    _body(pdf,
        "The backtest covers daily OHLCV data across multiple symbols, comparing "
        "DCA performance against a simple buy-and-hold baseline. Results include "
        "risk-adjusted returns (Sharpe ratio), maximum drawdown analysis, paired "
        "order completion statistics, and parameter sensitivity analysis."
    )

    # ── 2. Strategy Overview ─────────────────────────────────────────────
    _section(pdf, "2. Strategy Overview")
    _body(pdf,
        "The Pairwise DCA strategy works by continuously maintaining sell-stop "
        "orders on a fraction of the held position. When the market drops and "
        "triggers a sell, a paired limit-buy order is already in place to "
        "re-acquire the shares at a lower price, capturing the spread as a "
        "cost basis improvement."
    )

    _subsection(pdf, "Parameters")
    param_widths = [38, 22, 116]
    _pdf_table_header(pdf, ["Parameter", "Default", "Description"], param_widths)
    params_data = [
        ("stop_offset_pct", "1.0%", "Sell stop placed at open * (1 - offset)"),
        ("buy_offset", "$0.20", "Buy limit placed at stop_price - $0.20"),
        ("coverage_threshold", "20%", "Min fraction of position covered by pending pairs"),
        ("proximity_pct", "0.75%", "Skip new pair if within this % of existing pair"),
        ("lot_size", str(DEFAULT_LOT_SIZE), "Maximum shares per paired order"),
        ("sell_expiry_days", "30", "Trading days before unfilled sell is cancelled"),
        ("buy_expiry_days", "30", "Trading days before unfilled buy expires"),
    ]
    for row in params_data:
        _pdf_table_row(pdf, list(row), param_widths)

    _subsection(pdf, "Order Lifecycle")
    pdf.set_font("Courier", "", 9)
    lifecycle = (
        "PENDING --(bar.low <= stop)--> SELL_TRIGGERED --(bar.low <= buy)--> COMPLETED\n"
        "   |                                  |\n"
        "   | (30 days, no trigger)             | (30 days, no buy fill)\n"
        "   v                                  v\n"
        "CANCELLED                         SELL_ONLY"
    )
    for line in lifecycle.split("\n"):
        pdf.cell(0, 4.5, _safe(line), new_x="LMARGIN", new_y="NEXT")
    pdf.ln(3)

    # ── 3. Backtest Methodology ──────────────────────────────────────────
    _section(pdf, "3. Backtest Methodology")
    bullets = [
        "Data Source: Twelve Data API, daily OHLCV bars",
        "Resolution: Daily bars; stops trigger if bar.low <= stop_price",
        "Fill Assumption: Orders fill at limit price (no slippage), consistent with Robinhood's commission-free execution",
        "Initial Positions: Matched to live portfolio holdings at acquisition cost",
        "Rebalancing: None; the strategy only rotates within existing positions",
    ]
    for b in bullets:
        _bullet(pdf, b)

    # ── 4. Results per symbol ────────────────────────────────────────────
    _section(pdf, "4. Results")

    for sym in symbols:
        m = metrics[sym]
        _subsection(pdf, sym)

        stats = [
            f"Initial Value: ${m.initial_value:,.2f}",
            f"Final Value (DCA): ${m.final_net_value:,.2f}",
            f"Final Value (Buy & Hold): ${m.buy_hold_final_value:,.2f}",
            f"Total Return: {m.total_return_pct:+.2f}% (vs B&H: {m.buy_hold_return_pct:+.2f}%)",
            f"Annualized Return: {m.annualized_return_pct:+.2f}%",
            f"Sharpe Ratio: {m.sharpe_ratio:.3f}",
            f"Max Drawdown: {m.max_drawdown_pct:.2f}% ({m.max_drawdown_start} to {m.max_drawdown_end})",
        ]
        for s in stats:
            _bullet(pdf, s)

        # Embed charts
        for chart_name in ["equity_curves", "drawdown", "cost_basis"]:
            chart_path = CHARTS_DIR / f"{sym}_{chart_name}.png"
            if chart_path.exists():
                _embed_chart(pdf, chart_path)

    # ── 5. Paired Order Statistics ───────────────────────────────────────
    _section(pdf, "5. Paired Order Statistics")
    stat_widths = [22, 22, 28, 26, 28, 26, 24]
    _pdf_table_header(pdf, ["Symbol", "Placed", "Completed", "Sell Only", "Cancelled", "Rate", "Avg Days"], stat_widths)
    for sym in symbols:
        m = metrics[sym]
        _pdf_table_row(pdf, [
            sym, str(m.pairs_placed), str(m.pairs_completed),
            str(m.pairs_sell_only), str(m.pairs_cancelled),
            f"{m.completion_rate_pct:.1f}%", f"{m.avg_days_to_complete:.1f}",
        ], stat_widths)

    for sym in symbols:
        chart_path = CHARTS_DIR / f"{sym}_pair_activity.png"
        if chart_path.exists():
            _embed_chart(pdf, chart_path)

    # ── 6. Parameter Sensitivity ─────────────────────────────────────────
    if sensitivity_results:
        _section(pdf, "6. Parameter Sensitivity")
        _body(pdf, "Each parameter was varied individually while holding others at default values.")

        param_descriptions = {
            "stop_offset_pct": "Stop Offset (0.5% - 3.0%)",
            "buy_offset": "Buy Offset ($0.10 - $0.50)",
            "coverage_threshold": "Coverage Threshold (10% - 30%)",
        }

        for sym, params in sensitivity_results.items():
            _subsection(pdf, sym)
            for param_name, data in params.items():
                desc = param_descriptions.get(param_name, param_name)
                pdf.set_font("Helvetica", "BI", 10)
                pdf.cell(0, 7, _safe(desc), new_x="LMARGIN", new_y="NEXT")

                sens_widths = [28, 28, 22, 28, 22, 28]
                _pdf_table_header(pdf, ["Value", "Return", "Sharpe", "Drawdown", "Pairs", "Completion"], sens_widths)
                for d in data:
                    if param_name == "stop_offset_pct":
                        val_str = f"{d['param_value']*100:.1f}%"
                    elif param_name == "buy_offset":
                        val_str = f"${d['param_value']:.2f}"
                    elif param_name == "coverage_threshold":
                        val_str = f"{d['param_value']*100:.0f}%"
                    else:
                        val_str = str(d["param_value"])
                    _pdf_table_row(pdf, [
                        val_str, f"{d['total_return_pct']:+.2f}%",
                        f"{d['sharpe_ratio']:.3f}", f"{d['max_drawdown_pct']:.2f}%",
                        str(d["pairs_placed"]), f"{d['completion_rate_pct']:.1f}%",
                    ], sens_widths)

                chart_path = CHARTS_DIR / f"{sym}_sensitivity_{param_name}.png"
                if chart_path.exists():
                    _embed_chart(pdf, chart_path)

    # ── 7. Risk Analysis ─────────────────────────────────────────────────
    _section(pdf, "7. Risk Analysis")

    _subsection(pdf, "Drawdown Comparison")
    _body(pdf,
        "The pairwise DCA strategy may experience slightly higher drawdowns "
        "than buy-and-hold during sustained declines, because sell stops "
        "trigger (reducing shares) but buy limits may not fill if the decline "
        "continues beyond the buy offset."
    )

    _subsection(pdf, "Sustained Decline Scenario")
    _body(pdf,
        "In a sustained decline, the strategy repeatedly sells at stop prices "
        "but buy orders may expire unfilled (SELL_ONLY outcomes), resulting in "
        "gradual position reduction and accumulated cash. This acts as a "
        "natural de-risking mechanism but can lead to underperformance in a "
        "V-shaped recovery."
    )

    _subsection(pdf, "Sustained Rally Scenario")
    _body(pdf,
        "In a sustained rally, sell stops are never triggered (all pairs "
        "remain PENDING then CANCELLED). The strategy matches buy-and-hold "
        "exactly, with no cost basis improvement opportunity. No harm done, "
        "but no benefit either."
    )

    _subsection(pdf, "Cash Drag")
    _body(pdf,
        "When sell orders fill but buy orders have not yet filled, the "
        "strategy holds cash. This cash earns no return in the simulation "
        "(conservative assumption). In practice, Robinhood sweeps idle cash "
        "into money market funds, partially offsetting this drag."
    )

    _subsection(pdf, "Dollar-Amount Buy Offset")
    _body(pdf,
        "The $0.20 fixed buy_offset is significant for low-priced securities "
        "like BTC (Grayscale Bitcoin Mini Trust ETF at ~$31, representing "
        "0.65% spread) but negligible for SPY/QQQ (~$450, representing only "
        "0.04%). The parameter sensitivity analysis quantifies this asymmetry."
    )

    # ── 8. Limitations & Future Work ─────────────────────────────────────
    _section(pdf, "8. Limitations & Future Work")

    _subsection(pdf, "Limitations")
    limitations = [
        "Daily resolution: Intraday price action is compressed into OHLCV bars. A stop and buy could both trigger in the same bar, which may overstate completion rates.",
        "No slippage model: Fills assumed at exact limit prices. Real execution may experience minor slippage, especially in volatile markets.",
        "No partial fills: Each paired order fills completely or not at all.",
        "Fixed lot sizing: The simulation uses a fixed lot_size cap rather than the live system's dynamic position-proportional sizing.",
        "No dividends or corporate actions: SPY/QQQ dividends are not reinvested.",
    ]
    for i, lim in enumerate(limitations, 1):
        _bullet(pdf, f"{i}. {lim}")

    _subsection(pdf, "Future Work")
    future = [
        "Percentage-based buy_offset (e.g., 0.5% of stop price) to normalize across different price levels",
        "Intraday (5-min) bar resolution for more accurate fill simulation",
        "Monte Carlo analysis with randomized entry points",
        "Multi-asset correlation analysis (do all symbols benefit equally?)",
        "Transaction cost sensitivity (for non-Robinhood brokers)",
    ]
    for i, f_item in enumerate(future, 1):
        _bullet(pdf, f"{i}. {f_item}")

    # ── 9. Conclusion ────────────────────────────────────────────────────
    _section(pdf, "9. Conclusion")

    positive = [sym for sym in symbols if metrics[sym].excess_return_pct > 0]
    negative = [sym for sym in symbols if metrics[sym].excess_return_pct <= 0]

    if positive:
        _body(pdf,
            f"The Pairwise DCA strategy generated excess returns over buy-and-hold "
            f"for {', '.join(positive)}, demonstrating that the paired sell-stop / "
            f"buy-limit mechanism can capture mean-reversion opportunities in "
            f"volatile markets."
        )
    if negative:
        _body(pdf,
            f"For {', '.join(negative)}, the strategy underperformed buy-and-hold, "
            f"likely due to sustained directional moves that resulted in position "
            f"reduction without corresponding re-entry opportunities."
        )

    _body(pdf,
        "The strategy is most effective in range-bound or moderately volatile "
        "markets where prices oscillate within the stop-buy spread, allowing "
        "repeated cost basis improvements. It serves as a defensive overlay "
        "that naturally de-risks during sustained declines while matching "
        "buy-and-hold during rallies."
    )

    pdf.output(str(pdf_path))
    print(f"PDF written to {pdf_path}")
    return pdf_path


# PDF helpers are now imported from trading_system.backtests.pdf_helpers
