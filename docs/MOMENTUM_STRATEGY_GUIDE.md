# AAPL Momentum Trading Strategy Guide

## 📊 Strategy Overview

Your momentum strategy uses **moving average crossovers** to generate buy/sell signals for AAPL.

### How It Works

1. **Short-term MA (5 periods)**: Tracks recent price momentum
2. **Long-term MA (20 periods)**: Tracks overall trend
3. **Crossover Signals**:
   - **BUY**: Short MA crosses above Long MA (bullish momentum)
   - **SELL**: Short MA crosses below Long MA (bearish momentum)
   - **HOLD**: No crossover detected

### Current Status (Live Analysis)

```
Symbol: AAPL
Current Price: $248.36
Short MA (5): $248.60
Long MA (20): $249.69
Momentum: BEARISH
Signal: HOLD (bearish trend, staying out)
```

**Interpretation**: AAPL is currently in a bearish trend. The strategy recommends staying out until momentum turns bullish.

---

## 📈 Backtest Results (Last 3 Months)

```
Analyzing 62 trading days...

Date         Signal  Price     Short MA   Long MA
──────────────────────────────────────────────────
2025-11-20   SELL    $266.25   $268.42    $269.64
2025-11-25   BUY     $276.97   $271.84    $270.83
2025-12-17   SELL    $271.84   $275.37    $276.96

Total signals: 3
  BUY signals: 1
  SELL signals: 2
```

**Key Insights**:
- Strategy generated 3 signals over 3 months
- Low signal frequency = fewer trades, lower transaction costs
- Last signal was SELL on Dec 17 at $271.84

---

## 🚀 How to Use the Strategy

### 1. Run Backtest (Review Historical Performance)

```bash
cd ~/robinhood-trading
python momentum_strategy.py
```

This shows:
- Historical signals over last 3 months
- What signals would have been generated
- Current market signal

### 2. Check Current Signal (Dry Run)

```python
from momentum_strategy import MomentumStrategy

# Initialize in dry run mode
strategy = MomentumStrategy(
    symbol='AAPL',
    position_size_pct=0.10,  # 10% of cash per trade
    dry_run=True
)

# Run strategy
strategy.run_strategy(interval='10minute', span='week')
```

**Output**:
- Current signal (BUY/SELL/HOLD)
- Moving averages
- Momentum direction
- What action would be taken (dry run)

### 3. Execute Live Trading

```python
from momentum_strategy import MomentumStrategy

# LIVE TRADING - Set dry_run=False
strategy = MomentumStrategy(
    symbol='AAPL',
    position_size_pct=0.10,  # Use 10% of cash
    dry_run=False  # ⚠️ LIVE TRADING
)

# Run strategy (will execute real orders!)
strategy.run_strategy(interval='10minute', span='week')
```

⚠️ **Warning**: This will execute real orders if signals are generated!

---

## ⚙️ Strategy Parameters

| Parameter | Default | Description |
|-----------|---------|-------------|
| `symbol` | 'AAPL' | Stock ticker to trade |
| `position_size_pct` | 0.10 | % of cash to use (10% = $581 per trade) |
| `dry_run` | True | Set to False for live trading |
| `short_period` | 5 | Short MA period |
| `long_period` | 20 | Long MA period |

### Adjust Parameters

```python
strategy = MomentumStrategy(
    symbol='AAPL',
    position_size_pct=0.05,  # Use 5% of cash instead of 10%
    dry_run=True
)
```

---

## 📊 Position Sizing

With **$5,814.61** available cash and **10% position size**:

```
Position Value: $5,814.61 × 10% = $581.46
AAPL Price: $248.36
Shares to Buy: $581.46 / $248.36 = 2 shares
Total Cost: 2 × $248.36 = $496.72
```

**Conservative approach**: Only uses 10% of cash per trade, leaving 90% for other opportunities.

---

## 🎯 Strategy Logic (Step-by-Step)

### When to BUY

1. ✅ Short MA crosses **above** Long MA (bullish crossover)
2. ✅ No existing position in AAPL
3. ✅ Sufficient cash available (10% of $5,814.61)
4. ✅ Calculate quantity: Cash × 10% / Current Price
5. 🛒 Place limit buy order at current price + 0.5%

### When to SELL

1. ✅ Short MA crosses **below** Long MA (bearish crossover)
2. ✅ Have existing position in AAPL
3. 💵 Place limit sell order for entire position at current price - 0.5%

### When to HOLD

- No crossover detected
- Already have position and trend is still bullish
- No position and trend is bearish

---

## 🔄 Automated Trading (Schedule Strategy)

### Option 1: Run Manually

```bash
# Check signals every day
python momentum_strategy.py
```

### Option 2: Cron Job (Automated)

Run strategy automatically during market hours:

```bash
# Edit crontab
crontab -e

# Add this line to run every hour during market hours (9:30 AM - 4:00 PM EST)
30 9-16 * * 1-5 cd ~/robinhood-trading && /usr/bin/python3 momentum_strategy.py >> ~/robinhood-trading/logs/momentum.log 2>&1
```

### Option 3: Python Script Loop

```python
import time
from momentum_strategy import MomentumStrategy

strategy = MomentumStrategy(symbol='AAPL', dry_run=False)

while True:
    # Run strategy
    strategy.run_strategy()

    # Wait 1 hour
    print("Waiting 1 hour before next check...")
    time.sleep(3600)
```

---

## 📈 Example Scenario

### Scenario: Bullish Crossover Detected

```
🚀 RUNNING MOMENTUM STRATEGY
══════════════════════════════════════════════════════════════════════
Time: 2026-01-23 10:30:00
Symbol: AAPL

📊 SIGNAL ANALYSIS
══════════════════════════════════════════════════════════════════════
Current Price: $250.00
Short MA (5): $251.00
Long MA (20): $249.50
Momentum: BULLISH
Signal: BUY
Reason: Bullish crossover: Short MA crossed above Long MA

🛒 BUY SIGNAL EXECUTION
══════════════════════════════════════════════════════════════════════
   Account: 919433888
   Symbol: AAPL
   Quantity: 2 shares
   Limit Price: $251.25  (current + 0.5%)
   Total Cost: $502.50
   Validation: ✅ Order validated

✅ Order placed successfully!
   Order ID: abc-123-xyz
   State: confirmed
```

---

## ⚠️ Risk Management

### Built-in Safety Features

✅ **Position sizing**: Only uses 10% of cash per trade
✅ **Account isolation**: Only trades in account 919433888
✅ **Cash only**: No margin or borrowed funds
✅ **Limit orders**: Uses limit orders with 0.5% buffer
✅ **Dry run default**: Must explicitly enable live trading

### Risks to Consider

⚠️ **Whipsaws**: Moving averages can generate false signals in choppy markets
⚠️ **Lag**: MAs are lagging indicators, may miss early trends
⚠️ **Overnight gaps**: Strategy doesn't account for after-hours moves
⚠️ **Transaction costs**: Frequent signals can rack up fees (Robinhood is commission-free)

---

## 📊 Performance Tracking

### Manual Tracking

After each trade, record:
```python
# Get current position
position = strategy.get_current_position()

# Record entry/exit
print(f"Entry: {position['avg_buy_price']}")
print(f"Current: {position['current_price']}")
print(f"P/L: {position['profit_loss']}")
```

### Create Trading Log

```python
import json
from datetime import datetime

# Log trade
trade_log = {
    'timestamp': datetime.now().isoformat(),
    'symbol': 'AAPL',
    'action': 'BUY',
    'quantity': 2,
    'price': 250.00,
    'signal': 'Bullish crossover'
}

with open('trades.json', 'a') as f:
    f.write(json.dumps(trade_log) + '\n')
```

---

## 🎓 Advanced Customization

### Change Symbols

Trade different stocks:
```python
# Trade MSFT instead
strategy = MomentumStrategy(symbol='MSFT', dry_run=True)

# Trade multiple symbols
for symbol in ['AAPL', 'MSFT', 'GOOGL']:
    strategy = MomentumStrategy(symbol=symbol, dry_run=True)
    strategy.run_strategy()
```

### Adjust MA Periods

Modify the moving average periods in `momentum_strategy.py`:

```python
# In calculate_momentum_signals():
def calculate_momentum_signals(self, prices, short_period=3, long_period=10):
    # Faster signals with shorter periods
    # More signals, but potentially more whipsaws
```

### Add Filters

Enhance strategy with additional filters:

```python
def should_trade(self, signal_info):
    """Add additional filters before trading"""

    # Only trade if volume is high enough
    quote = r.stocks.get_quotes(self.symbol)[0]
    volume = int(float(quote.get('volume', 0)))

    if volume < 1000000:  # 1M shares
        return False, "Volume too low"

    # Only trade if price is above $100
    if signal_info['current_price'] < 100:
        return False, "Price too low"

    return True, "Filters passed"
```

---

## 🆘 Troubleshooting

### "Not enough data"
- Strategy needs 20+ price points
- Try using `span='week'` or `span='month'`

### "Insufficient cash"
- Reduce `position_size_pct` to 0.05 (5%)
- Or wait for more cash to settle

### "No signals generated"
- This is normal! Strategy waits for crossovers
- Check backtest to see historical signal frequency
- Consider adjusting MA periods for more signals

---

## 📚 Next Steps

1. ✅ **Review backtest** - Understand historical performance
2. ✅ **Run in dry run** - Test without risking money
3. ✅ **Paper trade** - Track signals manually for a week
4. ✅ **Start small** - Use 5% position size for first trades
5. ✅ **Monitor closely** - Check strategy daily at first
6. ✅ **Optimize** - Adjust parameters based on results

---

## 📝 Summary

**Your momentum strategy is ready to use!**

- ✅ Configured for AAPL
- ✅ Uses 10% of cash per trade ($581)
- ✅ Isolated to account 919433888
- ✅ Cash-only (no margin)
- ✅ Dry run mode by default
- ✅ Backtested on 3 months of data

**Current Signal**: HOLD (bearish trend)

Run `python momentum_strategy.py` to get started!
