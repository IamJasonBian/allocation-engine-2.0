# Quick Start: AAPL Momentum Strategy

## ⚡ TL;DR

Simple momentum strategy for AAPL using moving average crossovers.

**Current Signal**: HOLD (bearish trend - wait for bullish crossover)

---

## 🚀 Run Strategy (3 Commands)

### 1. Backtest (See Historical Signals)
```bash
cd ~/robinhood-trading
python momentum_strategy.py
```

### 2. Check Current Signal (Dry Run)
```python
from momentum_strategy import MomentumStrategy

strategy = MomentumStrategy('AAPL', dry_run=True)
strategy.run_strategy()
```

### 3. Execute Live Trading
```python
from momentum_strategy import MomentumStrategy

# ⚠️ LIVE TRADING
strategy = MomentumStrategy('AAPL', dry_run=False)
strategy.run_strategy()
```

---

## 📊 How It Works

| Signal | Condition | Action |
|--------|-----------|--------|
| **BUY** | Short MA crosses **above** Long MA | Buy 2 shares (~$500) |
| **SELL** | Short MA crosses **below** Long MA | Sell entire position |
| **HOLD** | No crossover | Do nothing |

**Current Status**:
- Price: $248.36
- Short MA: $248.60
- Long MA: $249.69
- **Bearish trend** → HOLD

---

## 💰 Position Sizing

```
Available Cash: $5,814.61
Position Size: 10%
Trade Amount: ~$581
AAPL Shares: 2 shares @ $248.36 = $496.72
```

---

## ⚙️ Configuration

Edit these in your code:

```python
MomentumStrategy(
    symbol='AAPL',           # Stock to trade
    position_size_pct=0.10,  # 10% of cash
    dry_run=True             # False for live
)
```

---

## 📈 Recent Backtest Results

```
Last 3 Months (62 trading days):
  Signals: 3 total
  BUY: 1 signal
  SELL: 2 signals

Last Signals:
  2025-11-20: SELL @ $266.25
  2025-11-25: BUY @ $276.97
  2025-12-17: SELL @ $271.84
```

Low frequency = conservative strategy

---

## ✅ Safety Checklist

- ✅ Account 919433888 only
- ✅ Cash only (no margin)
- ✅ 10% position size (conservative)
- ✅ Limit orders (not market)
- ✅ Dry run by default

---

## 🔄 Automate (Optional)

Run every hour during market hours:

```bash
crontab -e

# Add this line:
30 9-16 * * 1-5 cd ~/robinhood-trading && python3 momentum_strategy.py
```

---

## 📚 Full Documentation

- **Complete Guide**: `MOMENTUM_STRATEGY_GUIDE.md`
- **Bot Guide**: `ACCOUNT_919433888_README.md`
- **Setup**: `SETUP_COMPLETE.md`

---

## 🎯 Recommended Workflow

1. Run backtest: `python momentum_strategy.py`
2. Review signals from last 3 months
3. Check current signal
4. If satisfied, enable live trading: `dry_run=False`
5. Monitor daily until comfortable

---

**Strategy is ready! Start with `python momentum_strategy.py`**
