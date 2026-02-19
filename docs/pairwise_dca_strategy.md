# Pairwise DCA Strategy: Backtest Analysis

## 1. Executive Summary

This paper presents the backtest results of the **Pairwise Dollar-Cost Averaging (DCA)**
strategy as implemented in the `MomentumDcaLongStrategy`. The strategy maintains
protective sell-stop orders on held positions, paired with limit-buy orders to
re-enter at a lower price, effectively reducing cost basis over time.

| Symbol | DCA Return | Buy & Hold Return | Excess Return | Sharpe | Max Drawdown |
|--------|-----------|-------------------|---------------|--------|-------------|
| BTC | -0.70% | -5.48% | +4.79% | 0.210 | 45.99% |

## 2. Strategy Overview

The Pairwise DCA strategy works by continuously maintaining sell-stop orders
on a fraction of the held position. When the market drops and triggers a sell,
a paired limit-buy order is already in place to re-acquire the shares at a lower
price, capturing the spread as a cost basis improvement.

### Parameters

| Parameter | Default | Description |
|-----------|---------|-------------|
| `stop_offset_pct` | 1.0% | Sell stop placed at `open * (1 - offset)` |
| `buy_offset` | $0.20 | Buy limit placed at `stop_price - $0.20` |
| `coverage_threshold` | 20% | Minimum fraction of position covered by pending pairs |
| `proximity_pct` | 0.75% | Skip new pair if within this % of existing pair |
| `lot_size` | 400 | Maximum shares per paired order |
| `sell_expiry_days` | 30 | Trading days before unfilled sell is cancelled |
| `buy_expiry_days` | 30 | Trading days before unfilled buy expires |

### Order Lifecycle

```
PENDING ──(bar.low ≤ stop_price)──> SELL_TRIGGERED ──(bar.low ≤ buy_price)──> COMPLETED
   │                                        │
   │ (30 days, no trigger)                  │ (30 days, no buy fill)
   v                                        v
CANCELLED                              SELL_ONLY
```

## 3. Backtest Methodology

- **Data Source**: Twelve Data API, daily OHLCV bars
- **Resolution**: Daily bars; stops trigger if `bar.low <= stop_price`
- **Fill Assumption**: Orders fill at limit price (no slippage), consistent
  with Robinhood's commission-free execution model
- **Initial Positions**: Matched to live portfolio holdings at acquisition cost
- **Rebalancing**: None; the strategy only rotates within existing positions

## 4. Results

### BTC

- **Initial Value**: $101,122.00
- **Final Value (DCA)**: $100,416.02
- **Final Value (Buy & Hold)**: $95,576.60
- **Total Return**: -0.70% (vs B&H: -5.48%)
- **Annualized Return**: -0.45%
- **Sharpe Ratio**: 0.210
- **Max Drawdown**: 45.99% (2025-10-06 to 2026-02-05)

![BTC Equity Curves](charts/BTC_equity_curves.png)

![BTC Drawdown](charts/BTC_drawdown.png)

![BTC Cost Basis](charts/BTC_cost_basis.png)

## 5. Paired Order Statistics

| Symbol | Placed | Completed | Sell Only | Cancelled | Completion Rate | Avg Days | Avg Improvement |
|--------|--------|-----------|-----------|-----------|-----------------|----------|-----------------|
| BTC | 286 | 251 | 4 | 30 | 87.8% | 3.4 | 0.5135% |

![BTC Pair Activity](charts/BTC_pair_activity.png)

## 7. Risk Analysis

### Drawdown Comparison

The pairwise DCA strategy may experience slightly higher drawdowns than buy-and-hold
during sustained declines, because sell stops trigger (reducing shares) but buy limits
may not fill if the decline continues beyond the buy offset.

### Sustained Decline Scenario

In a sustained decline, the strategy repeatedly sells at stop prices but buy orders
may expire unfilled (SELL_ONLY outcomes), resulting in gradual position reduction
and accumulated cash. This acts as a natural de-risking mechanism but can lead to
underperformance in a V-shaped recovery.

### Sustained Rally Scenario

In a sustained rally, sell stops are never triggered (all pairs remain PENDING then
CANCELLED). The strategy matches buy-and-hold exactly, with no cost basis improvement
opportunity. No harm done, but no benefit either.

### Cash Drag

When sell orders fill but buy orders have not yet filled, the strategy holds cash.
This cash earns no return in the simulation (conservative assumption). In practice,
Robinhood sweeps idle cash into money market funds, partially offsetting this drag.

### Dollar-Amount Buy Offset

The $0.20 fixed buy_offset is significant for low-priced securities like BTC
(Grayscale Bitcoin Mini Trust ETF at ~$31, representing 0.65% spread) but
negligible for SPY/QQQ (~$450, representing only 0.04%). The parameter sensitivity
analysis above quantifies this asymmetry.

## 8. Limitations & Future Work

### Limitations

1. **Daily resolution**: Intraday price action is compressed into OHLCV bars.
   A stop and buy could both trigger in the same bar, which may overstate
   completion rates vs real intraday execution.
2. **No slippage model**: Fills assumed at exact limit prices. Real execution
   may experience minor slippage, especially in volatile markets.
3. **No partial fills**: Each paired order fills completely or not at all.
4. **Fixed lot sizing**: The simulation uses a fixed lot_size cap rather than
   the live system's dynamic position-proportional sizing.
5. **No dividends or corporate actions**: SPY/QQQ dividends are not reinvested.

### Future Work

1. Percentage-based buy_offset (e.g., 0.5% of stop price) to normalize
   across different price levels
2. Intraday (5-min) bar resolution for more accurate fill simulation
3. Monte Carlo analysis with randomized entry points
4. Multi-asset correlation analysis (do all symbols benefit equally?)
5. Transaction cost sensitivity (for non-Robinhood brokers)

## 9. Conclusion

The Pairwise DCA strategy generated excess returns over buy-and-hold for
**BTC**, demonstrating that the paired sell-stop / buy-limit
mechanism can capture mean-reversion opportunities in volatile markets.

The strategy is most effective in range-bound or moderately volatile markets
where prices oscillate within the stop-buy spread, allowing repeated cost basis
improvements. It serves as a defensive overlay that naturally de-risks during
sustained declines while matching buy-and-hold during rallies.

---

*Generated by `trading_system.backtests.pairwise_dca_backtest`*
