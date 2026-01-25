# Trading Bot for Account 491498655 (Cash Only)

## ✅ Configuration Updated

Bot is now locked to **account 491498655**.

---

## 📊 Account Status

```
Account Number: 491498655
Type: Cash (no margin)
Available Cash: $379.79
Total Equity: $1,943.41

Current Positions:
• QQQ: 2.52 shares @ $620.50 (+15.77% / +$212.89)
```

---

## 🚀 Place a Single Order (Interactive)

### **Method 1: Interactive Menu**

```bash
cd ~/robinhood-trading
python place_single_order.py
```

**What it does**:
1. Shows your portfolio
2. Asks for stock symbol
3. Shows current price
4. Asks BUY or SELL
5. Asks quantity
6. Asks limit price
7. Confirms before executing

**Example session**:
```
📝 ORDER DETAILS
══════════════════════════════════════════════════════════════════════

Enter stock symbol (e.g., AAPL): AAPL

Fetching current price for AAPL...
Current price: $248.90

Order type:
  1. BUY
  2. SELL
Select (1 or 2): 1

Enter quantity (number of shares): 1

Enter limit price (press Enter for $249.15):

⚠️  EXECUTION MODE
══════════════════════════════════════════════════════════════════════

1. DRY RUN (simulate order)
2. LIVE (execute real order)

Select mode (1 or 2): 1

📋 ORDER SUMMARY
══════════════════════════════════════════════════════════════════════
   Account: 491498655
   Type: BUY
   Symbol: AAPL
   Quantity: 1
   Limit Price: $249.15
   Total: $249.15
   Mode: DRY RUN
══════════════════════════════════════════════════════════════════════

Confirm order? (yes/no): yes
```

---

### **Method 2: Quick Command Line**

```bash
# Dry run
python place_single_order.py AAPL 1 250.00 buy

# Live order
python place_single_order.py AAPL 1 250.00 buy --live
```

**Syntax**:
```
python place_single_order.py <SYMBOL> <QUANTITY> <PRICE> <buy|sell> [--live]
```

**Examples**:
```bash
# Buy 1 AAPL at $250 (dry run)
python place_single_order.py AAPL 1 250.00 buy

# Buy 1 AAPL at market price (dry run)
python place_single_order.py AAPL 1

# Sell 1 QQQ at $620 (dry run)
python place_single_order.py QQQ 1 620.00 sell

# Buy 1 AAPL at $250 (LIVE)
python place_single_order.py AAPL 1 250.00 buy --live
```

---

### **Method 3: Python Script**

```python
from place_single_order import quick_order

# Dry run
quick_order('AAPL', 1, 250.00, 'buy', dry_run=True)

# Live order
quick_order('AAPL', 1, 250.00, 'buy', dry_run=False)

# Auto-price (uses current price + 0.5%)
quick_order('AAPL', 1, order_type='buy', dry_run=True)
```

---

## 💰 Position Sizing for Account 491498655

With **$379.79** available cash:

| Stock | Current Price | Max Shares | Total Cost |
|-------|---------------|------------|------------|
| AAPL | $248.90 | 1 share | $248.90 |
| MSFT | ~$450 | 0 shares | N/A (insufficient) |
| SPY | ~$600 | 0 shares | N/A (insufficient) |
| TQQQ | ~$75 | 5 shares | $375.00 |

**Recommendation**: With limited cash, focus on:
- Single shares of mid-priced stocks (AAPL, GOOGL)
- Multiple shares of lower-priced ETFs (TQQQ, SQQQ)
- Fractional shares (if Robinhood supports for this account)

---

## 📈 Run Momentum Strategy

The momentum strategy will adjust position sizes automatically:

```bash
# Dry run (check signal)
python momentum_strategy.py

# Live trading (with small position size)
python momentum_strategy.py --position-size 0.50 --live
```

With $379.79 cash:
- **10% position** = $37.98 (fractional shares only)
- **50% position** = $189.90 (can buy 1 share of stocks under $190)
- **80% position** = $303.83 (larger positions)

---

## 🎯 Recommended First Order

### **Conservative Approach**

```bash
# Check AAPL price
python safe_cash_bot.py

# Place 1 share order (dry run first)
python place_single_order.py AAPL 1 249.00 buy

# If satisfied, execute live
python place_single_order.py AAPL 1 249.00 buy --live
```

**Cost**: ~$249
**Remaining cash**: ~$131

---

## 🛡️ Safety Checks

Before executing any order:

✅ **Check available cash**:
```bash
python safe_cash_bot.py
```

✅ **Verify account isolation**:
```bash
python verify_isolation.py
```

✅ **Test in dry run first**:
```bash
python place_single_order.py AAPL 1 250.00 buy
```

✅ **Review order details** before confirming

---

## 📊 Monitor Your Orders

### **Check Portfolio**
```bash
python safe_cash_bot.py
```

### **Check Robinhood App**
- Open Robinhood mobile app
- Go to Account → History
- View recent orders and executions

---

## 🔄 Order States

When you place an order, it goes through these states:

1. **Queued** - Order submitted to exchange
2. **Confirmed** - Exchange received order
3. **Partially Filled** - Some shares executed
4. **Filled** - Order completely executed
5. **Cancelled** - Order cancelled (manually or by system)
6. **Rejected** - Order rejected (insufficient funds, invalid symbol, etc.)

**Limit orders** only execute when the stock reaches your limit price!

---

## 💡 Example Workflow

### **First Order**

```bash
# 1. Check portfolio
python safe_cash_bot.py

# 2. Interactive order placement
python place_single_order.py
```

When prompted:
- Symbol: `AAPL`
- Type: `1` (BUY)
- Quantity: `1`
- Price: Press Enter (uses current + 0.5%)
- Mode: `1` (DRY RUN)
- Confirm: `yes`

Review the dry run output. If satisfied:

```bash
# 3. Execute live order
python place_single_order.py
```

Repeat steps, but select `2` (LIVE) when asked for mode.

```bash
# 4. Check if order filled
python safe_cash_bot.py
```

---

## ⚠️ Important Notes

1. **Limited Cash**: You only have $379.79, plan carefully
2. **Limit Orders**: Orders only execute at or better than your limit price
3. **Market Hours**: Orders execute during market hours (9:30 AM - 4:00 PM ET)
4. **Settlement**: Sold shares take 2 days to settle (T+2)
5. **Account Type**: Cash account, no day trading restrictions

---

## 🆘 Troubleshooting

### **"Insufficient cash"**
- You're trying to buy more than $379.79 worth
- Reduce quantity or choose cheaper stock

### **"No position in {symbol}"**
- You're trying to sell a stock you don't own
- Check positions: `python safe_cash_bot.py`

### **"Order rejected"**
- Price too far from market price
- Symbol doesn't exist
- Market is closed

### **Order not filling**
- Limit price not reached
- Check current price vs your limit
- Consider adjusting limit price

---

## 📚 Files

| File | Purpose |
|------|---------|
| `place_single_order.py` | Interactive order placement |
| `safe_cash_bot.py` | Bot API (for scripting) |
| `momentum_strategy.py` | Automated strategy |
| `verify_isolation.py` | Verify account lock |

---

## ✅ Ready to Trade

**Your bot is configured for account 491498655!**

**Start with**:
```bash
python place_single_order.py
```

Follow the prompts to place your first order.

**Remember**: Start with DRY RUN mode, then switch to LIVE when comfortable!
