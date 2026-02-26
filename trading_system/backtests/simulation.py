"""
Core backtest simulation engine for the Pairwise DCA strategy.

Replays daily OHLCV bars and simulates paired sell-stop / buy-limit orders
matching the MomentumDcaLongStrategy logic.
"""

from dataclasses import dataclass, field
from enum import Enum
from typing import Dict, List, Optional

from trading_system.config import DEFAULT_LOT_SIZE


class PairState(Enum):
    PENDING = "PENDING"               # sell stop placed, not yet triggered
    SELL_TRIGGERED = "SELL_TRIGGERED"  # sell filled, buy limit active
    COMPLETED = "COMPLETED"           # both sell and buy filled
    SELL_ONLY = "SELL_ONLY"           # sell filled but buy expired unfilled
    CANCELLED = "CANCELLED"           # sell never triggered, expired


@dataclass
class PairedOrder:
    sell_stop_price: float
    buy_limit_price: float
    quantity: int
    placed_date: str
    placed_bar_idx: int
    state: PairState = PairState.PENDING
    sell_fill_date: Optional[str] = None
    sell_fill_price: Optional[float] = None
    buy_fill_date: Optional[str] = None
    buy_fill_price: Optional[float] = None


@dataclass
class DailySnapshot:
    date: str
    price: float
    shares: int
    cash: float
    net_value: float
    cost_basis: float
    coverage_pct: float
    pairs_pending: int
    pairs_active: int  # SELL_TRIGGERED (buy side waiting)


@dataclass
class PortfolioState:
    shares: int
    cash: float
    cost_basis: float
    total_invested: float  # tracks total capital deployed
    pairs_placed: int = 0
    pairs_completed: int = 0
    pairs_sell_only: int = 0
    pairs_cancelled: int = 0


@dataclass
class SimulationResult:
    symbol: str
    snapshots: List[DailySnapshot]
    pairs: List[PairedOrder]
    portfolio: PortfolioState
    initial_shares: int
    initial_price: float


def run_simulation(
    bars: List[Dict],
    symbol: str,
    initial_shares: int,
    initial_price: float,
    stop_offset_pct: float = 0.01,
    buy_offset: float = 0.20,
    coverage_threshold: float = 0.20,
    proximity_pct: float = 0.0075,
    lot_size: int = DEFAULT_LOT_SIZE,
    sell_expiry_days: int = 30,
    buy_expiry_days: int = 30,
) -> SimulationResult:
    """
    Run pairwise DCA simulation over daily bars.

    Args:
        bars: List of {date, open, high, low, close, volume} in chronological order
        symbol: Ticker symbol (for labelling)
        initial_shares: Starting share count
        initial_price: Price at which shares were acquired (for cost basis)
        stop_offset_pct: Sell stop placed at open * (1 - offset)
        buy_offset: Buy limit placed at (stop_price - buy_offset)
        coverage_threshold: Minimum fraction of position covered by pending pairs
        proximity_pct: Skip new pair if within this % of existing pending pair
        lot_size: Max shares per paired order
        sell_expiry_days: Trading days before unfilled sell is cancelled
        buy_expiry_days: Trading days before unfilled buy expires (SELL_ONLY)

    Returns:
        SimulationResult with daily snapshots and all paired orders
    """
    portfolio = PortfolioState(
        shares=initial_shares,
        cash=0.0,
        cost_basis=initial_price,
        total_invested=initial_shares * initial_price,
    )
    pairs: List[PairedOrder] = []
    snapshots: List[DailySnapshot] = []

    for bar_idx, bar in enumerate(bars):
        date = bar["date"]
        bar_open = bar["open"]
        bar_high = bar["high"]
        bar_low = bar["low"]
        bar_close = bar["close"]

        # ── Step 1: Process existing orders ──────────────────────────────
        for pair in pairs:
            # Check sell trigger on PENDING orders
            if pair.state == PairState.PENDING:
                if bar_low <= pair.sell_stop_price:
                    # Realistic fill modeling
                    if bar_open < pair.sell_stop_price:
                        # Gap-through: price opened below stop
                        if bar_open >= pair.buy_limit_price:
                            # Fill at open (slipped)
                            pair.sell_fill_price = bar_open
                        else:
                            # Gapped through both stop AND limit — no fill
                            continue
                    else:
                        # Normal trigger: small slippage based on bar range
                        bar_range = bar_high - bar_low
                        slippage = bar_range * 0.01  # 1% of daily range
                        pair.sell_fill_price = max(
                            pair.sell_stop_price - slippage,
                            pair.sell_stop_price * 0.995
                        )
                    pair.state = PairState.SELL_TRIGGERED
                    pair.sell_fill_date = date
                    portfolio.shares -= pair.quantity
                    portfolio.cash += pair.quantity * pair.sell_fill_price

            # Check buy fill on SELL_TRIGGERED orders (can happen same bar)
            if pair.state == PairState.SELL_TRIGGERED:
                if bar_low <= pair.buy_limit_price:
                    pair.state = PairState.COMPLETED
                    pair.buy_fill_date = date
                    # Buy fills at limit (this is realistic for limit buys)
                    pair.buy_fill_price = pair.buy_limit_price
                    portfolio.shares += pair.quantity
                    portfolio.cash -= pair.quantity * pair.buy_fill_price
                    portfolio.pairs_completed += 1
                    # Update cost basis: weighted average
                    _update_cost_basis(portfolio, pair)

        # ── Step 2: Expire stale orders ──────────────────────────────────
        for pair in pairs:
            if pair.state == PairState.PENDING:
                days_since = bar_idx - pair.placed_bar_idx
                if days_since >= sell_expiry_days:
                    pair.state = PairState.CANCELLED
                    portfolio.pairs_cancelled += 1

            elif pair.state == PairState.SELL_TRIGGERED:
                # Count days since sell filled
                sell_bar = _find_bar_idx_by_date(bars, pair.sell_fill_date, pair.placed_bar_idx)
                if sell_bar is not None:
                    days_since_sell = bar_idx - sell_bar
                    if days_since_sell >= buy_expiry_days:
                        pair.state = PairState.SELL_ONLY
                        portfolio.pairs_sell_only += 1

        # ── Step 3: Coverage check & new pair placement ──────────────────
        active_pairs = [p for p in pairs if p.state == PairState.PENDING]
        covered_shares = sum(p.quantity for p in active_pairs)
        coverage_pct = (covered_shares / portfolio.shares * 100) if portfolio.shares > 0 else 100.0

        if coverage_pct < coverage_threshold * 100 and portfolio.shares > 0:
            stop_price = round(bar_open * (1 - stop_offset_pct), 2)
            buy_price = round(stop_price - buy_offset, 2)

            # Proximity check: skip if too close to existing pending pair
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
                    new_pair = PairedOrder(
                        sell_stop_price=stop_price,
                        buy_limit_price=buy_price,
                        quantity=gap_qty,
                        placed_date=date,
                        placed_bar_idx=bar_idx,
                    )
                    pairs.append(new_pair)
                    portfolio.pairs_placed += 1

        # ── Step 4: Record daily snapshot ────────────────────────────────
        net_value = portfolio.shares * bar_close + portfolio.cash
        pending_count = sum(1 for p in pairs if p.state == PairState.PENDING)
        active_count = sum(1 for p in pairs if p.state == PairState.SELL_TRIGGERED)

        snapshots.append(DailySnapshot(
            date=date,
            price=bar_close,
            shares=portfolio.shares,
            cash=portfolio.cash,
            net_value=net_value,
            cost_basis=portfolio.cost_basis,
            coverage_pct=coverage_pct,
            pairs_pending=pending_count,
            pairs_active=active_count,
        ))

    return SimulationResult(
        symbol=symbol,
        snapshots=snapshots,
        pairs=pairs,
        portfolio=portfolio,
        initial_shares=initial_shares,
        initial_price=initial_price,
    )


def _update_cost_basis(portfolio: PortfolioState, pair: PairedOrder):
    """Update weighted-average cost basis after a completed pair."""
    if portfolio.shares <= 0:
        return
    # The pair sold at sell_fill_price and bought back at buy_fill_price.
    # The cost basis improvement per share is (sell_price - buy_price) spread
    # across the full position as a weighted average.
    # Simpler: track total invested vs total shares.
    # After sell: total_invested reduced by (quantity * old_cost_basis)
    # After buy: total_invested increased by (quantity * buy_price)
    old_invested = portfolio.total_invested
    # Remove cost of sold shares (at original cost basis before the pair)
    old_invested -= pair.quantity * portfolio.cost_basis
    # Add cost of bought shares (at buy fill price)
    old_invested += pair.quantity * pair.buy_fill_price
    portfolio.total_invested = old_invested
    portfolio.cost_basis = portfolio.total_invested / portfolio.shares


def _find_bar_idx_by_date(bars: List[Dict], target_date: str, start_idx: int) -> Optional[int]:
    """Find bar index matching a date, searching from start_idx."""
    for i in range(start_idx, len(bars)):
        if bars[i]["date"] == target_date:
            return i
    return start_idx  # fallback to placed bar
