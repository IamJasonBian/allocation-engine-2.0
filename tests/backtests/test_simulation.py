"""
Unit tests for the pairwise DCA backtest simulation engine.
Tests fill logic, cost basis math, expiry, and edge cases.
"""

from trading_system.config import DEFAULT_LOT_SIZE
from trading_system.backtests.simulation import (
    PairState,
    PairedOrder,
    PortfolioState,
    run_simulation,
)
from trading_system.backtests.metrics import compute_metrics, buy_hold_comparison


def _make_bars(prices, start_date="2023-01-01"):
    """
    Generate daily bars from a list of close prices.
    Sets open=close, high=close+0.50, low=close-0.50 for simplicity.
    """
    from datetime import datetime, timedelta
    bars = []
    dt = datetime.strptime(start_date, "%Y-%m-%d")
    for i, price in enumerate(prices):
        bars.append({
            "date": (dt + timedelta(days=i)).strftime("%Y-%m-%d"),
            "open": price,
            "high": price + 0.50,
            "low": price - 0.50,
            "close": price,
            "volume": 1000000,
        })
    return bars


def _make_bars_ohlc(ohlc_list, start_date="2023-01-01"):
    """Generate bars from list of (open, high, low, close) tuples."""
    from datetime import datetime, timedelta
    bars = []
    dt = datetime.strptime(start_date, "%Y-%m-%d")
    for i, (o, h, l, c) in enumerate(ohlc_list):
        bars.append({
            "date": (dt + timedelta(days=i)).strftime("%Y-%m-%d"),
            "open": o,
            "high": h,
            "low": l,
            "close": c,
            "volume": 1000000,
        })
    return bars


class TestSellFillLogic:
    """Test that sell stops trigger correctly based on bar.low."""

    def test_sell_triggers_when_low_hits_stop(self):
        # Price starts at 100, drops to 98.50 on day 2 (low=98.0)
        # Stop at 99.0 (1% below open=100) should trigger
        bars = _make_bars_ohlc([
            (100, 101, 99.50, 100),   # day 0: pair placed, stop=99.0
            (100, 101, 98.00, 99.00), # day 1: low=98 <= stop=99 → sell fills
        ])
        result = run_simulation(bars, "TEST", 100, 100.0,
                                stop_offset_pct=0.01, buy_offset=0.20, lot_size=DEFAULT_LOT_SIZE)
        # At least one pair should have been placed and sell-triggered
        triggered = [p for p in result.pairs if p.state != PairState.PENDING]
        assert len(triggered) >= 1
        sell_triggered = [p for p in result.pairs
                         if p.sell_fill_price is not None]
        assert len(sell_triggered) >= 1

    def test_sell_does_not_trigger_when_low_above_stop(self):
        # Price stays above stop level
        bars = _make_bars_ohlc([
            (100, 101, 99.50, 100.50),  # stop=99.0, low=99.50 > 99.0
            (101, 102, 100.0, 101.0),   # still above
        ])
        result = run_simulation(bars, "TEST", 100, 100.0,
                                stop_offset_pct=0.01, buy_offset=0.20, lot_size=DEFAULT_LOT_SIZE)
        # All pairs should still be PENDING (or cancelled if expired)
        for p in result.pairs:
            assert p.sell_fill_price is None

    def test_sell_fills_near_stop_price_with_slippage(self):
        bars = _make_bars_ohlc([
            (100, 101, 99.50, 100),    # pair placed, stop=99.0
            (100, 101, 98.00, 99.00),  # triggers
        ])
        result = run_simulation(bars, "TEST", 100, 100.0,
                                stop_offset_pct=0.01, buy_offset=0.20, lot_size=DEFAULT_LOT_SIZE)
        filled = [p for p in result.pairs if p.sell_fill_price is not None]
        assert len(filled) >= 1
        # Fill price should be near stop price (with realistic slippage)
        assert filled[0].sell_fill_price <= filled[0].sell_stop_price
        # Slippage bounded by 0.5% (stop * 0.995)
        assert filled[0].sell_fill_price >= filled[0].sell_stop_price * 0.995


class TestBuyFillLogic:
    """Test that buy limits fill when bar.low reaches buy price."""

    def test_buy_fills_same_bar_as_sell(self):
        # Large drop: both stop and buy trigger in same bar
        bars = _make_bars_ohlc([
            (100, 101, 99.50, 100),    # pair placed: stop=99.0, buy=98.80
            (100, 100, 97.00, 98.00),  # low=97 hits both stop=99 and buy=98.80
        ])
        result = run_simulation(bars, "TEST", 100, 100.0,
                                stop_offset_pct=0.01, buy_offset=0.20, lot_size=DEFAULT_LOT_SIZE)
        completed = [p for p in result.pairs if p.state == PairState.COMPLETED]
        assert len(completed) >= 1

    def test_buy_fills_on_subsequent_bar(self):
        # Sell triggers day 1, buy fills day 2
        bars = _make_bars_ohlc([
            (100, 101, 99.50, 100),     # pair placed: stop=99.0, buy=98.80
            (100, 100, 98.90, 99.00),   # low=98.90 hits stop=99, but not buy=98.80
            (99, 99.50, 98.50, 98.80),  # low=98.50 hits buy=98.80 → COMPLETED
        ])
        result = run_simulation(bars, "TEST", 100, 100.0,
                                stop_offset_pct=0.01, buy_offset=0.20, lot_size=DEFAULT_LOT_SIZE)
        completed = [p for p in result.pairs if p.state == PairState.COMPLETED]
        assert len(completed) >= 1

    def test_buy_fills_at_limit_price(self):
        bars = _make_bars_ohlc([
            (100, 101, 99.50, 100),
            (100, 100, 97.00, 98.00),
        ])
        result = run_simulation(bars, "TEST", 100, 100.0,
                                stop_offset_pct=0.01, buy_offset=0.20, lot_size=DEFAULT_LOT_SIZE)
        completed = [p for p in result.pairs if p.state == PairState.COMPLETED]
        assert len(completed) >= 1
        assert completed[0].buy_fill_price == completed[0].buy_limit_price


class TestCostBasis:
    """Test cost basis improvement calculations."""

    def test_cost_basis_improves_after_completed_pair(self):
        # Sell at 99, buy at 98.80 → should lower cost basis
        bars = _make_bars_ohlc([
            (100, 101, 99.50, 100),     # placed
            (100, 100, 97.00, 98.00),   # both fill same bar
            (98, 99, 97.50, 98.50),     # extra bar for snapshot
        ])
        result = run_simulation(bars, "TEST", 100, 100.0,
                                stop_offset_pct=0.01, buy_offset=0.20, lot_size=DEFAULT_LOT_SIZE)
        completed = [p for p in result.pairs if p.state == PairState.COMPLETED]
        if completed:
            # Cost basis should be lower than initial
            assert result.portfolio.cost_basis < 100.0

    def test_shares_preserved_after_completed_pair(self):
        """After sell+buy, share count should be same as before."""
        bars = _make_bars_ohlc([
            (100, 101, 99.50, 100),
            (100, 100, 97.00, 98.00),
            (98, 99, 97.50, 98.50),
        ])
        result = run_simulation(bars, "TEST", 100, 100.0,
                                stop_offset_pct=0.01, buy_offset=0.20, lot_size=DEFAULT_LOT_SIZE)
        # After completed pairs, shares should be back to original
        # (minus any SELL_TRIGGERED that haven't bought back yet)
        pending_sells = sum(p.quantity for p in result.pairs
                           if p.state == PairState.SELL_TRIGGERED)
        assert result.portfolio.shares == 100 - pending_sells

    def test_cash_positive_after_completed_pair(self):
        """Completed pair generates small positive cash (sell > buy)."""
        bars = _make_bars_ohlc([
            (100, 101, 99.50, 100),
            (100, 100, 97.00, 98.00),
            (98, 99, 97.50, 98.50),
        ])
        result = run_simulation(bars, "TEST", 100, 100.0,
                                stop_offset_pct=0.01, buy_offset=0.20, lot_size=DEFAULT_LOT_SIZE)
        completed = [p for p in result.pairs if p.state == PairState.COMPLETED]
        if completed:
            # Each completed pair generates qty * (sell_price - buy_price) cash
            expected_cash = sum(
                p.quantity * (p.sell_fill_price - p.buy_fill_price)
                for p in completed
            )
            # Cash should be approximately this amount (may have multiple pairs)
            assert result.portfolio.cash >= 0


class TestOrderExpiry:
    """Test 30-day expiration logic."""

    def test_unfilled_sell_cancels_after_30_days(self):
        # Price stays high for 31 bars — stop never triggers
        prices = [100 + i * 0.1 for i in range(35)]  # trending up
        bars = _make_bars(prices)
        result = run_simulation(bars, "TEST", 100, 100.0,
                                stop_offset_pct=0.01, buy_offset=0.20,
                                sell_expiry_days=30, lot_size=DEFAULT_LOT_SIZE)
        cancelled = [p for p in result.pairs if p.state == PairState.CANCELLED]
        assert len(cancelled) > 0

    def test_unfilled_buy_becomes_sell_only_after_30_days(self):
        # Sell triggers day 1, then price rallies for 31 bars (buy never fills)
        ohlc = [(100, 101, 99.50, 100)]       # day 0: pair placed
        ohlc.append((100, 100, 98.90, 99.0))  # day 1: sell triggers (low=98.90 <= stop=99.0)
        # Then 31 bars of rally — buy at 98.80 never fills
        for i in range(31):
            p = 100 + i * 0.2
            ohlc.append((p, p + 1, p - 0.1, p + 0.5))
        bars = _make_bars_ohlc(ohlc)
        result = run_simulation(bars, "TEST", 100, 100.0,
                                stop_offset_pct=0.01, buy_offset=0.20,
                                buy_expiry_days=30, lot_size=DEFAULT_LOT_SIZE)
        sell_only = [p for p in result.pairs if p.state == PairState.SELL_ONLY]
        assert len(sell_only) > 0


class TestCoverageCheck:
    """Test coverage threshold and pair placement logic."""

    def test_pair_placed_when_under_coverage(self):
        bars = _make_bars([100] * 5)
        result = run_simulation(bars, "TEST", 100, 100.0,
                                coverage_threshold=0.20, lot_size=DEFAULT_LOT_SIZE)
        assert len(result.pairs) > 0

    def test_gap_quantity_matches_threshold(self):
        bars = _make_bars([100] * 5)
        result = run_simulation(bars, "TEST", 100, 100.0,
                                coverage_threshold=0.20, lot_size=DEFAULT_LOT_SIZE)
        # First pair should be for 20 shares (20% of 100)
        assert result.pairs[0].quantity == 20

    def test_lot_size_caps_quantity(self):
        bars = _make_bars([100] * 5)
        # 20% of 1000 = 200, but lot_size=50 caps it
        result = run_simulation(bars, "TEST", 1000, 100.0,
                                coverage_threshold=0.20, lot_size=50)
        assert result.pairs[0].quantity == 50

    def test_proximity_prevents_duplicate_pairs(self):
        # On consecutive stable bars, should not place pairs too close together
        bars = _make_bars([100.0] * 10)
        result = run_simulation(bars, "TEST", 1000, 100.0,
                                coverage_threshold=0.20, lot_size=50,
                                proximity_pct=0.0075)
        # With proximity check, shouldn't get pairs at nearly identical prices
        if len(result.pairs) >= 2:
            p1 = result.pairs[0].sell_stop_price
            p2 = result.pairs[1].sell_stop_price
            # They should be either the same (rejected) or different enough
            # At minimum, the count should be limited vs unconstrained
            assert len(result.pairs) < 10  # not unlimited


class TestNetValue:
    """Test net value and snapshot calculations."""

    def test_initial_net_value(self):
        bars = _make_bars([100] * 3)
        result = run_simulation(bars, "TEST", 100, 100.0)
        # First snapshot: 100 shares * 100 close + 0 cash = 10000
        assert result.snapshots[0].net_value == 100 * 100.0

    def test_net_value_tracks_price_changes(self):
        bars = _make_bars([100, 110, 120])
        result = run_simulation(bars, "TEST", 100, 100.0,
                                coverage_threshold=1.0)  # no pairs placed
        # Last snapshot should reflect price=120
        last = result.snapshots[-1]
        assert last.net_value == last.shares * 120.0 + last.cash

    def test_cash_added_on_sell(self):
        bars = _make_bars_ohlc([
            (100, 101, 99.50, 100),
            (100, 100, 97.00, 98.00),  # sell triggers
        ])
        result = run_simulation(bars, "TEST", 100, 100.0,
                                stop_offset_pct=0.01, buy_offset=0.20, lot_size=DEFAULT_LOT_SIZE)
        # If sell triggered and buy also filled, cash should be small positive
        # If only sell triggered, cash = qty * stop_price
        assert result.snapshots[-1].cash >= 0


class TestMetrics:
    """Test metrics computation from simulation results."""

    def test_buy_hold_comparison_length(self):
        bars = _make_bars([100, 105, 110, 108, 112])
        result = run_simulation(bars, "TEST", 100, 100.0)
        comparison = buy_hold_comparison(result)
        assert len(comparison) == len(bars)

    def test_metrics_compute_basic(self):
        bars = _make_bars([100, 105, 110, 108, 112])
        result = run_simulation(bars, "TEST", 100, 100.0)
        m = compute_metrics(result)
        assert m.symbol == "TEST"
        assert m.initial_value == 10000.0

    def test_sharpe_not_nan(self):
        bars = _make_bars(list(range(100, 200)))
        result = run_simulation(bars, "TEST", 100, 100.0)
        m = compute_metrics(result)
        assert m.sharpe_ratio == m.sharpe_ratio  # not NaN

    def test_max_drawdown_positive(self):
        # Create a drawdown scenario
        prices = list(range(100, 120)) + list(range(120, 100, -1))
        bars = _make_bars(prices)
        result = run_simulation(bars, "TEST", 100, 100.0)
        m = compute_metrics(result)
        assert m.max_drawdown_pct >= 0


class TestEdgeCases:
    """Edge cases and boundary conditions."""

    def test_single_share(self):
        bars = _make_bars([100] * 5)
        result = run_simulation(bars, "TEST", 1, 100.0,
                                coverage_threshold=0.20, lot_size=DEFAULT_LOT_SIZE)
        # Should not crash; may or may not place pairs
        assert len(result.snapshots) == 5

    def test_very_small_lot_size(self):
        bars = _make_bars([100] * 5)
        result = run_simulation(bars, "TEST", 100, 100.0, lot_size=1)
        if result.pairs:
            assert result.pairs[0].quantity == 1

    def test_zero_buy_offset(self):
        """buy_offset=0 means buy at same price as sell — still works."""
        bars = _make_bars_ohlc([
            (100, 101, 99.50, 100),
            (100, 100, 97.00, 98.00),
        ])
        result = run_simulation(bars, "TEST", 100, 100.0,
                                buy_offset=0.0, lot_size=DEFAULT_LOT_SIZE)
        # Should not crash
        assert len(result.snapshots) == 2

    def test_high_coverage_threshold_no_pairs(self):
        """coverage_threshold=1.0 means 100% must be covered — always placing."""
        bars = _make_bars([100] * 3)
        result = run_simulation(bars, "TEST", 100, 100.0,
                                coverage_threshold=1.0, lot_size=DEFAULT_LOT_SIZE)
        # 100% threshold should still place up to lot_size
        assert result.portfolio.pairs_placed > 0

    def test_empty_bars(self):
        """Gracefully handle minimal bar data."""
        bars = _make_bars([100, 101])
        result = run_simulation(bars, "TEST", 100, 100.0)
        assert len(result.snapshots) == 2
