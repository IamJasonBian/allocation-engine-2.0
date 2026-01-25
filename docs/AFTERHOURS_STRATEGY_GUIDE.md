# After-Hours Daily Trading Strategy

## 📊 Strategy Overview

**Simple daily AAPL trading strategy:**
- **4:00 PM ET**: Buy AAPL at market close
- **9:30 AM ET (next day)**: Sell AAPL at market open
- **Goal**: Capture overnight price movement

---

## 🎯 How It Works

### **Buy Phase (Market Close)**
- **Window**: 3:55 PM - 4:15 PM ET
- **Action**: Buy AAPL with 90% of available cash
- **Price**: Limit order at current price + 0.5%
- **Quantity**: Maximum shares affordable

### **Sell Phase (Market Open)**
- **Window**: 9:25 AM - 9:45 AM ET
- **Action**: Sell entire AAPL position
- **Price**: Limit order at current price - 0.5%
- **Result**: Lock in overnight profit/loss

### **State Tracking**
Strategy remembers:
- Last buy date (prevents double-buying)
- Last sell date (prevents double-selling)
- Position held status
- Buy price for P/L calculation

---

## 🚀 Usage

### **1. Test Strategy (Dry Run)**

```bash
cd ~/robinhood-trading
python afterhours_daily_strategy.py
```

**What it does**:
- Checks current time
- Determines if in buy/sell window
- Shows what action would be taken
- **No real orders executed**

---

### **2. Run Live Trading**

```bash
python afterhours_daily_strategy.py --live
```

**5-second warning before execution!**

---

### **3. Check Performance**

```bash
python afterhours_daily_strategy.py --performance
```

Shows:
- Last buy/sell dates
- Current position status
- Historical performance

---

## ⏰ Trading Windows

| Time | Action | Window | What Happens |
|------|--------|--------|--------------|
| **9:25-9:45 AM** | SELL | Market Open | Sell yesterday's position |
| **3:55-4:15 PM** | BUY | Market Close | Buy today's position |
| **Other times** | WAIT | - | Nothing happens |

**Current time**: Strategy automatically detects ET timezone

---

## 💰 Position Sizing (Account 491498655)

With **$379.79** available cash:

```
Max Investment: $379.79 × 90% = $341.81
AAPL Price: ~$249
Shares to Buy: 1 share
Total Cost: ~$249
Remaining: ~$131
```

Strategy uses **90% of cash** (conservative buffer for price movements)

---

## 📅 Example Day

### **Morning (9:30 AM)**
```
🚀 RUNNING AFTER-HOURS DAILY STRATEGY
══════════════════════════════════════════════════════════════════════
   Time: 2026-01-23 09:30:00 EST
   Market Status: Market hours

⏰ SELL WINDOW (Market Open)

💵 SELL EXECUTION
   Symbol: AAPL
   Quantity: 1
   Buy Price: $248.50
   Current Price: $251.20
   P/L: +$2.70 (+1.09%)

✅ Order placed successfully!
```

### **Afternoon (4:00 PM)**
```
🚀 RUNNING AFTER-HOURS DAILY STRATEGY
══════════════════════════════════════════════════════════════════════
   Time: 2026-01-23 16:00:00 EST
   Market Status: After market close

⏰ BUY WINDOW (Market Close)

🛒 BUY EXECUTION
   Symbol: AAPL
   Quantity: 1
   Current Price: $250.80
   Limit Price: $252.05

✅ Order placed successfully!
```

---

## 🔄 Automation (Cron Jobs)

### **Set Up Automatic Trading**

```bash
crontab -e
```

Add these lines:

```bash
# Sell at market open (9:30 AM ET Monday-Friday)
30 9 * * 1-5 cd ~/robinhood-trading && python3 afterhours_daily_strategy.py --live >> logs/afterhours.log 2>&1

# Buy at market close (4:00 PM ET Monday-Friday)
0 16 * * 1-5 cd ~/robinhood-trading && python3 afterhours_daily_strategy.py --live >> logs/afterhours.log 2>&1
```

**Create logs directory first:**
```bash
mkdir -p ~/robinhood-trading/logs
```

---

## 📊 State File

Strategy saves state to `afterhours_state.json`:

```json
{
  "last_buy_date": "2026-01-23",
  "last_sell_date": "2026-01-23",
  "position_held": false,
  "buy_price": 250.80,
  "buy_quantity": 1
}
```

**Safety**: Prevents double-buying or double-selling on same day

---

## 💡 Strategy Logic

### **Prevents Mistakes**

✅ **Already bought today?** → Skip buy signal
✅ **No position to sell?** → Skip sell signal
✅ **Insufficient cash?** → Skip buy signal
✅ **Already sold today?** → Skip sell signal

### **Smart Position Sizing**

```python
# Uses 90% of available cash
max_investment = available_cash * 0.90
quantity = int(max_investment / current_price)

# Minimum 1 share
if quantity < 1:
    return "Insufficient cash"
```

---

## 📈 Expected Performance

### **Profit Scenarios**

| Overnight Move | Profit/Loss (1 share @ $250) |
|----------------|------------------------------|
| +1% | +$2.50 |
| +0.5% | +$1.25 |
| 0% | $0.00 |
| -0.5% | -$1.25 |
| -1% | -$2.50 |

**Historical**: AAPL overnight moves average ±0.5-1.5%

### **Risks**

⚠️ **Overnight gaps**: Significant news can cause large moves
⚠️ **Market volatility**: Earnings, Fed announcements
⚠️ **Weekend risk**: Friday close → Monday open gap
⚠️ **Limited upside**: Only captures overnight movement

---

## 🛡️ Safety Features

✅ **Account isolation**: Only trades in 491498655
✅ **Cash only**: No margin
✅ **State tracking**: Prevents double orders
✅ **Time windows**: Only trades at specific times
✅ **Limit orders**: Not market orders (0.5% buffer)
✅ **Dry run default**: Must use --live flag

---

## 🎓 Advanced Options

### **Change Symbol**

```bash
# Trade MSFT instead
python afterhours_daily_strategy.py --symbol MSFT --live
```

### **Manual Override**

Delete state file to reset:
```bash
rm afterhours_state.json
```

### **Monitor Logs**

```bash
tail -f ~/robinhood-trading/logs/afterhours.log
```

---

## 📋 Daily Checklist

### **Morning (Before 9:30 AM)**
1. ✅ Check if yesterday's buy executed
2. ✅ Verify position exists
3. ✅ Check pre-market AAPL price
4. ✅ Strategy will auto-sell at 9:30 AM

### **Evening (Before 4:00 PM)**
1. ✅ Check if morning sell executed
2. ✅ Verify cash available
3. ✅ Check AAPL closing price
4. ✅ Strategy will auto-buy at 4:00 PM

---

## 🆘 Troubleshooting

### **"Already bought today"**
- State file shows buy already executed
- Wait for tomorrow's buy window
- Or delete `afterhours_state.json` to reset

### **"No position to sell"**
- Yesterday's buy didn't execute
- Check order history in Robinhood app
- Verify limit price was reached

### **"Insufficient cash"**
- Not enough cash for 1 share
- Add funds to account
- Or trade cheaper stock (--symbol TQQQ)

### **Order not filling**
- Limit price not reached
- Market too volatile
- Adjust buffer in code (1% instead of 0.5%)

---

## 📊 Performance Tracking

Create a tracking spreadsheet:

| Date | Buy Price | Sell Price | P/L | P/L % |
|------|-----------|------------|-----|-------|
| 2026-01-23 | $250.80 | $251.20 | +$0.40 | +0.16% |
| 2026-01-24 | $251.50 | $253.00 | +$1.50 | +0.60% |
| 2026-01-27 | $253.20 | $252.80 | -$0.40 | -0.16% |

**Track**:
- Win rate (% profitable days)
- Average profit per trade
- Total return
- Sharpe ratio

---

## 🎯 Summary

**Your after-hours strategy is ready!**

✅ **Buy** AAPL at 4:00 PM
✅ **Sell** AAPL at 9:30 AM next day
✅ **Automated** with cron jobs
✅ **Safe** with state tracking

**Test it:**
```bash
python afterhours_daily_strategy.py
```

**Go live:**
```bash
python afterhours_daily_strategy.py --live
```

**Automate it:**
```bash
crontab -e
# Add the cron jobs from above
```

Good luck! 🚀
