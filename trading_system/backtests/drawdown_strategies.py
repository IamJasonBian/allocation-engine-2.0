"""
Drawdown-focused strategy simulations.

Three strategies designed for continued BTC drawdown scenarios:
  1. Cash Reserve — pause DCA during drawdowns, resume on recovery
  2. Trailing Stop — full exit via trailing stop, re-enter at discount
  3. Adaptive Sizing — scale lot size inversely with rolling volatility
"""

import math
from dataclasses import dataclass, field
from typing import Dict, List, Optional

from trading_system.backtests.simulation import run_simulation


@dataclass
class DrawdownStrategyResult:
    """Unified result for drawdown strategy simulations."""
    strategy_name: str
    dates: List[str]
    values: List[float]
    metadata: Dict  # strategy-specific info (params, signals, etc.)


# ---------------------------------------------------------------------------
# 1. Cash Reserve Strategy
# ---------------------------------------------------------------------------

def run_cash_reserve_strategy(
    bars: List[Dict],
    shares: int,
    price: float,
    drawdown_trigger: float = 0.10,
    recovery_signal_pct: float = 0.05,
) -> DrawdownStrategyResult:
    """
    Pause DCA pairing when the portfolio drops >drawdown_trigger from its
    rolling peak. Resume when price recovers recovery_signal_pct from the
    trough.

    Internally runs the normal simulation but tracks a "paused" flag.
    During paused windows, coverage_threshold is set to 0 (no new pairs).
    """
    if not bars or len(bars) < 2:
        return DrawdownStrategyResult("Cash Reserve", [], [], {})

    initial_value = shares * price
    peak_value = initial_value
    trough_price = bars[0]["close"]
    paused = False
    pause_count = 0

    # We'll run the simulation in segments, toggling coverage_threshold.
    # Simpler approach: run the full sim ourselves bar-by-bar by delegating
    # to run_simulation on sub-windows, but that resets state. Instead,
    # replicate the portfolio logic with pausing.

    from trading_system.backtests.simulation import (
        PairState,
        PairedOrder,
        PortfolioState,
        DailySnapshot,
    )

    portfolio = PortfolioState(
        shares=shares,
        cash=0.0,
        cost_basis=price,
        total_invested=shares * price,
    )
    pairs: List[PairedOrder] = []
    dates: List[str] = []
    values: List[float] = []

    stop_offset_pct = 0.01
    buy_offset = 0.20
    coverage_threshold = 0.20
    proximity_pct = 0.0075
    lot_size = 100
    sell_expiry_days = 30
    buy_expiry_days = 30

    for bar_idx, bar in enumerate(bars):
        date = bar["date"]
        bar_open = bar["open"]
        bar_low = bar["low"]
        bar_close = bar["close"]

        # Process existing orders (always — even when paused)
        for pair in pairs:
            if pair.state == PairState.PENDING:
                if bar_low <= pair.sell_stop_price:
                    pair.state = PairState.SELL_TRIGGERED
                    pair.sell_fill_date = date
                    pair.sell_fill_price = pair.sell_stop_price
                    portfolio.shares -= pair.quantity
                    portfolio.cash += pair.quantity * pair.sell_stop_price

            if pair.state == PairState.SELL_TRIGGERED:
                if bar_low <= pair.buy_limit_price:
                    pair.state = PairState.COMPLETED
                    pair.buy_fill_date = date
                    pair.buy_fill_price = pair.buy_limit_price
                    portfolio.shares += pair.quantity
                    portfolio.cash -= pair.quantity * pair.buy_limit_price
                    portfolio.pairs_completed += 1

        # Expire stale orders
        for pair in pairs:
            if pair.state == PairState.PENDING:
                if bar_idx - pair.placed_bar_idx >= sell_expiry_days:
                    pair.state = PairState.CANCELLED
                    portfolio.pairs_cancelled += 1
            elif pair.state == PairState.SELL_TRIGGERED:
                sell_bar = pair.placed_bar_idx
                for j in range(pair.placed_bar_idx, len(bars)):
                    if bars[j]["date"] == pair.sell_fill_date:
                        sell_bar = j
                        break
                if bar_idx - sell_bar >= buy_expiry_days:
                    pair.state = PairState.SELL_ONLY
                    portfolio.pairs_sell_only += 1

        # Portfolio value check for pause/resume
        net_value = portfolio.shares * bar_close + portfolio.cash
        if net_value > peak_value:
            peak_value = net_value

        drawdown_from_peak = (peak_value - net_value) / peak_value if peak_value > 0 else 0

        if not paused and drawdown_from_peak >= drawdown_trigger:
            paused = True
            trough_price = bar_close
            pause_count += 1
        elif paused:
            if bar_close < trough_price:
                trough_price = bar_close
            recovery = (bar_close - trough_price) / trough_price if trough_price > 0 else 0
            if recovery >= recovery_signal_pct:
                paused = False

        # Place new pairs only when NOT paused
        if not paused and portfolio.shares > 0:
            active_pairs = [p for p in pairs if p.state == PairState.PENDING]
            covered_shares = sum(p.quantity for p in active_pairs)
            coverage_pct = (covered_shares / portfolio.shares * 100) if portfolio.shares > 0 else 100.0

            if coverage_pct < coverage_threshold * 100:
                stop_price = round(bar_open * (1 - stop_offset_pct), 2)
                buy_price = round(stop_price - buy_offset, 2)

                too_close = False
                for p in active_pairs:
                    if p.sell_stop_price > 0:
                        dist = abs(stop_price - p.sell_stop_price) / p.sell_stop_price
                        if dist <= proximity_pct:
                            too_close = True
                            break

                if not too_close and buy_price > 0:
                    gap_qty = int(coverage_threshold * portfolio.shares) - covered_shares
                    gap_qty = max(1, min(gap_qty, lot_size, portfolio.shares - covered_shares))
                    if gap_qty > 0:
                        pairs.append(PairedOrder(
                            sell_stop_price=stop_price,
                            buy_limit_price=buy_price,
                            quantity=gap_qty,
                            placed_date=date,
                            placed_bar_idx=bar_idx,
                        ))
                        portfolio.pairs_placed += 1

        dates.append(date)
        values.append(net_value)

    return DrawdownStrategyResult(
        strategy_name="Cash Reserve",
        dates=dates,
        values=values,
        metadata={
            "drawdown_trigger": drawdown_trigger,
            "recovery_signal_pct": recovery_signal_pct,
            "pause_count": pause_count,
            "pairs_placed": portfolio.pairs_placed,
            "pairs_completed": portfolio.pairs_completed,
        },
    )


# ---------------------------------------------------------------------------
# 2. Trailing Stop Strategy
# ---------------------------------------------------------------------------

def run_trailing_stop_strategy(
    bars: List[Dict],
    shares: int,
    price: float,
    trail_pct: float = 0.08,
    reentry_drop_pct: float = 0.05,
) -> DrawdownStrategyResult:
    """
    Full exit via trailing stop (trail_pct from rolling high).
    Re-enter when price drops reentry_drop_pct below exit price.

    This is a pure position-level strategy — no DCA pairing.
    """
    if not bars or len(bars) < 2:
        return DrawdownStrategyResult("Trailing Stop", [], [], {})

    current_shares = shares
    cash = 0.0
    peak_price = bars[0]["close"]
    exit_price: Optional[float] = None
    in_position = True
    exit_count = 0
    reentry_count = 0

    dates: List[str] = []
    values: List[float] = []

    for bar in bars:
        bar_close = bar["close"]
        bar_low = bar["low"]

        if in_position:
            if bar_close > peak_price:
                peak_price = bar_close

            trail_level = peak_price * (1 - trail_pct)
            if bar_low <= trail_level:
                # Exit at trail level (or bar_low if gap down)
                fill_price = max(bar_low, trail_level)
                cash += current_shares * fill_price
                exit_price = fill_price
                current_shares = 0
                in_position = False
                exit_count += 1
        else:
            # Waiting to re-enter
            reentry_level = exit_price * (1 - reentry_drop_pct)
            if bar_low <= reentry_level:
                # Re-enter at reentry_level (or bar_low if gap down)
                fill_price = max(bar_low, reentry_level)
                if fill_price > 0:
                    current_shares = int(cash / fill_price)
                    cash -= current_shares * fill_price
                    peak_price = fill_price  # reset peak for new trailing stop
                    in_position = True
                    reentry_count += 1

        net_value = current_shares * bar_close + cash
        dates.append(bar["date"])
        values.append(net_value)

    return DrawdownStrategyResult(
        strategy_name="Trailing Stop",
        dates=dates,
        values=values,
        metadata={
            "trail_pct": trail_pct,
            "reentry_drop_pct": reentry_drop_pct,
            "exit_count": exit_count,
            "reentry_count": reentry_count,
            "final_in_position": in_position,
            "final_shares": current_shares,
            "final_cash": round(cash, 2),
        },
    )


# ---------------------------------------------------------------------------
# 3. Adaptive Sizing Strategy
# ---------------------------------------------------------------------------

def run_adaptive_sizing_strategy(
    bars: List[Dict],
    shares: int,
    price: float,
    vol_window: int = 21,
) -> DrawdownStrategyResult:
    """
    Run DCA but dynamically scale lot_size inversely with rolling volatility.

    High vol -> smaller lots (less exposed to whipsaw)
    Low vol  -> larger lots (capture more of the move)

    Base lot_size = 100. Scaling factor = median_vol / current_vol,
    clamped to [0.25, 2.0].
    """
    if not bars or len(bars) < 2:
        return DrawdownStrategyResult("Adaptive Sizing", [], [], {})

    # Pre-compute rolling daily returns volatility
    closes = [b["close"] for b in bars]
    daily_rets = []
    for i in range(1, len(closes)):
        if closes[i - 1] > 0:
            daily_rets.append((closes[i] - closes[i - 1]) / closes[i - 1])
        else:
            daily_rets.append(0.0)

    # Rolling annualized vol for each bar (bar_idx >= 1)
    rolling_vols: List[float] = []
    for i in range(len(daily_rets)):
        start = max(0, i - vol_window + 1)
        window_rets = daily_rets[start: i + 1]
        if len(window_rets) >= 2:
            mean_r = sum(window_rets) / len(window_rets)
            var = sum((r - mean_r) ** 2 for r in window_rets) / (len(window_rets) - 1)
            std = math.sqrt(var) if var > 0 else 0.01
            ann_vol = std * math.sqrt(252)
        else:
            ann_vol = 0.20  # default
        rolling_vols.append(ann_vol)

    # Median vol for scaling reference
    sorted_vols = sorted(rolling_vols)
    median_vol = sorted_vols[len(sorted_vols) // 2] if sorted_vols else 0.20

    # Run simulation with adaptive lot sizes — we run segments
    # Actually, just replicate the sim with variable lot_size per bar.
    from trading_system.backtests.simulation import (
        PairState,
        PairedOrder,
        PortfolioState,
    )

    portfolio = PortfolioState(
        shares=shares,
        cash=0.0,
        cost_basis=price,
        total_invested=shares * price,
    )
    pairs: List[PairedOrder] = []
    dates: List[str] = []
    values: List[float] = []
    lot_sizes_used: List[int] = []

    stop_offset_pct = 0.01
    buy_offset = 0.20
    coverage_threshold = 0.20
    proximity_pct = 0.0075
    base_lot_size = 100
    sell_expiry_days = 30
    buy_expiry_days = 30

    for bar_idx, bar in enumerate(bars):
        date = bar["date"]
        bar_open = bar["open"]
        bar_low = bar["low"]
        bar_close = bar["close"]

        # Process existing orders
        for pair in pairs:
            if pair.state == PairState.PENDING:
                if bar_low <= pair.sell_stop_price:
                    pair.state = PairState.SELL_TRIGGERED
                    pair.sell_fill_date = date
                    pair.sell_fill_price = pair.sell_stop_price
                    portfolio.shares -= pair.quantity
                    portfolio.cash += pair.quantity * pair.sell_stop_price

            if pair.state == PairState.SELL_TRIGGERED:
                if bar_low <= pair.buy_limit_price:
                    pair.state = PairState.COMPLETED
                    pair.buy_fill_date = date
                    pair.buy_fill_price = pair.buy_limit_price
                    portfolio.shares += pair.quantity
                    portfolio.cash -= pair.quantity * pair.buy_limit_price
                    portfolio.pairs_completed += 1

        # Expire stale orders
        for pair in pairs:
            if pair.state == PairState.PENDING:
                if bar_idx - pair.placed_bar_idx >= sell_expiry_days:
                    pair.state = PairState.CANCELLED
                    portfolio.pairs_cancelled += 1
            elif pair.state == PairState.SELL_TRIGGERED:
                sell_bar = pair.placed_bar_idx
                for j in range(pair.placed_bar_idx, len(bars)):
                    if bars[j]["date"] == pair.sell_fill_date:
                        sell_bar = j
                        break
                if bar_idx - sell_bar >= buy_expiry_days:
                    pair.state = PairState.SELL_ONLY
                    portfolio.pairs_sell_only += 1

        # Adaptive lot size
        if bar_idx >= 1:
            vol_idx = bar_idx - 1  # rolling_vols[0] corresponds to bar_idx=1
            if vol_idx < len(rolling_vols) and rolling_vols[vol_idx] > 0:
                scale = median_vol / rolling_vols[vol_idx]
                scale = max(0.25, min(2.0, scale))
            else:
                scale = 1.0
        else:
            scale = 1.0
        adaptive_lot = max(1, int(base_lot_size * scale))

        # Place new pairs with adaptive lot size
        if portfolio.shares > 0:
            active_pairs = [p for p in pairs if p.state == PairState.PENDING]
            covered_shares = sum(p.quantity for p in active_pairs)
            coverage_pct = (covered_shares / portfolio.shares * 100) if portfolio.shares > 0 else 100.0

            if coverage_pct < coverage_threshold * 100:
                stop_price = round(bar_open * (1 - stop_offset_pct), 2)
                buy_price = round(stop_price - buy_offset, 2)

                too_close = False
                for p in active_pairs:
                    if p.sell_stop_price > 0:
                        dist = abs(stop_price - p.sell_stop_price) / p.sell_stop_price
                        if dist <= proximity_pct:
                            too_close = True
                            break

                if not too_close and buy_price > 0:
                    gap_qty = int(coverage_threshold * portfolio.shares) - covered_shares
                    gap_qty = max(1, min(gap_qty, adaptive_lot, portfolio.shares - covered_shares))
                    if gap_qty > 0:
                        pairs.append(PairedOrder(
                            sell_stop_price=stop_price,
                            buy_limit_price=buy_price,
                            quantity=gap_qty,
                            placed_date=date,
                            placed_bar_idx=bar_idx,
                        ))
                        portfolio.pairs_placed += 1
                        lot_sizes_used.append(gap_qty)

        net_value = portfolio.shares * bar_close + portfolio.cash
        dates.append(date)
        values.append(net_value)

    avg_lot = sum(lot_sizes_used) / len(lot_sizes_used) if lot_sizes_used else base_lot_size

    return DrawdownStrategyResult(
        strategy_name="Adaptive Sizing",
        dates=dates,
        values=values,
        metadata={
            "vol_window": vol_window,
            "base_lot_size": base_lot_size,
            "median_vol": round(median_vol, 4),
            "avg_adaptive_lot": round(avg_lot, 1),
            "pairs_placed": portfolio.pairs_placed,
            "pairs_completed": portfolio.pairs_completed,
        },
    )
