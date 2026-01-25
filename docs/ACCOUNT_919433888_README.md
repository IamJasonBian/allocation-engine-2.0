# Trading Bot for Account 919433888 (Cash Only)

## 🔒 Isolation Features

Your trading bot is now **strictly isolated** to account `919433888`:

✅ **Account locked** - All operations target only account 919433888
✅ **Cash only** - Uses available cash, never margin
✅ **Pre-validated orders** - Checks cash balance before execution
✅ **Safe by default** - Dry run mode prevents accidental trades
✅ **Position tracking** - Only shows positions in target account

## 📋 Quick Start

### 1. Verify Isolation

Run this first to confirm everything is set up correctly:

```bash
cd ~/robinhood-trading
python verify_isolation.py
```

Expected output:
```
🔍 ISOLATION VERIFICATION
══════════════════════════════════════════════════════════════════════
Target Account: 919433888
✅ VERIFIED: Cash account (no margin)
✅ ISOLATION VERIFIED
Bot is locked to account: 919433888
```

### 2. Run the Safe Cash Bot

```bash
python safe_cash_bot.py
```

This will:
- Login to account 919433888
- Show your portfolio summary
- Display available cash
- Run example dry run order

## 💡 Usage Examples

### Get Portfolio Summary

```python
from safe_cash_bot import SafeCashBot

bot = SafeCashBot()
portfolio = bot.get_portfolio_summary()
```

Output:
```
💼 PORTFOLIO SUMMARY - ACCOUNT 919433888
══════════════════════════════════════════════════════════════════════

💰 Cash Balances:
   Available Cash: $2,000.00
   Buying Power: $2,000.00
   Withdrawable: $1,500.00

📊 Portfolio:
   Total Equity: $10,543.21
   Market Value: $8,543.21

📈 Positions: 2
   AAPL
      Quantity: 5
      Current: $175.50
      P/L: +$127.50 (+17.00%)
```

### Get Real-Time Quote

```python
bot = SafeCashBot()
price = bot.get_quote('AAPL')
```

### Place Buy Order (Dry Run)

```python
bot = SafeCashBot()

# DRY RUN - simulates order, doesn't execute
bot.place_cash_buy_order('AAPL', 1, 150.00, dry_run=True)
```

Output:
```
🛒 BUY ORDER - DRY RUN
══════════════════════════════════════════════════════════════════════
   Account: 919433888
   Symbol: AAPL
   Quantity: 1
   Limit Price: $150.00
   Total Cost: $150.00
   Validation: ✅ Order validated

⚠️  DRY RUN MODE - Order not executed
```

### Place Real Buy Order (LIVE)

```python
bot = SafeCashBot()

# LIVE ORDER - actually executes
bot.place_cash_buy_order('AAPL', 1, 150.00, dry_run=False)
```

⚠️ **WARNING**: This places a real order and will use real money!

### Sell Position

```python
bot = SafeCashBot()

# Check current position first
portfolio = bot.get_portfolio_summary()

# Sell 1 share (dry run)
bot.place_sell_order('AAPL', 1, 175.00, dry_run=True)

# Sell 1 share (LIVE)
bot.place_sell_order('AAPL', 1, 175.00, dry_run=False)
```

## 🛡️ Safety Features

### 1. Account Number Verification

The bot verifies it's using account 919433888 on startup:

```python
if self.account_number != "919433888":
    print(f"⚠️  WARNING: Expected account 919433888, got {self.account_number}")
    # Prompts for confirmation
```

### 2. Cash Balance Validation

Before every buy order:

```python
available_cash = cash_info['tradeable_cash']
total_cost = quantity * price * 1.01  # 1% buffer

if total_cost > available_cash:
    return False, "Insufficient cash"
```

### 3. Position Validation

Before every sell order:

```python
if quantity > current_position:
    return False, "Insufficient shares"
```

### 4. Dry Run by Default

All order functions require explicit `dry_run=False` to execute:

```python
def place_cash_buy_order(self, symbol, quantity, price, dry_run=True):
    # dry_run=True is the default
```

## 📊 Available Methods

### Portfolio & Account

| Method | Description |
|--------|-------------|
| `get_cash_balance()` | Get available cash (not buying power) |
| `get_portfolio_summary()` | Full portfolio with positions |
| `get_positions()` | List of all open positions |
| `get_quote(symbol)` | Real-time stock quote |

### Trading

| Method | Parameters | Description |
|--------|------------|-------------|
| `place_cash_buy_order()` | symbol, quantity, price, dry_run | Buy with cash only |
| `place_sell_order()` | symbol, quantity, price, dry_run | Sell existing position |
| `validate_buy_order()` | symbol, quantity, price | Check if order is valid |

## 🔐 Configuration

Your `.env` file is configured:

```bash
# Account locked to 919433888
RH_AUTOMATED_ACCOUNT_NUMBER=919433888
```

**NEVER change this value** unless you want to use a different account.

## ⚠️ Important Warnings

1. **Dry Run First**: Always test with `dry_run=True` before executing real orders
2. **Cash Only**: Even if your account has margin, the bot only uses available cash
3. **Limit Orders**: All orders are limit orders, not market orders
4. **Price Changes**: Stock prices can change between validation and execution
5. **Fees**: Orders may incur fees depending on your Robinhood account type

## 🧪 Testing Workflow

**Recommended workflow before live trading:**

```bash
# 1. Verify isolation
python verify_isolation.py

# 2. Run the bot in dry run mode
python safe_cash_bot.py

# 3. Test custom orders in Python
python
>>> from safe_cash_bot import SafeCashBot
>>> bot = SafeCashBot()
>>> bot.get_portfolio_summary()
>>> bot.place_cash_buy_order('AAPL', 1, 150.00, dry_run=True)

# 4. Only after testing, execute real orders
>>> bot.place_cash_buy_order('AAPL', 1, 150.00, dry_run=False)
```

## 📁 Related Files

- `safe_cash_bot.py` - Main trading bot (cash only, account 919433888)
- `verify_isolation.py` - Verify account isolation
- `.env` - Configuration (account number stored here)
- `rh_auth.py` - Authentication manager

## 🆘 Troubleshooting

### "Cannot access account 919433888"
- Run `python verify_isolation.py` to check account access
- Verify credentials in `.env` file
- Check if account is still active on Robinhood

### "Insufficient cash"
- Check available cash: `bot.get_cash_balance()`
- You may have pending orders holding cash
- Unsettled funds may not be available

### "No position in {symbol}"
- Check positions: `bot.get_positions()`
- You must own the stock before selling
- Verify you're looking at the correct account

## 🚀 Next Steps

1. ✅ Run `python verify_isolation.py` to confirm setup
2. ✅ Run `python safe_cash_bot.py` to see your portfolio
3. ✅ Test dry run orders before going live
4. ✅ Build your trading strategy!

---

**Remember**: This bot only trades in account 919433888 using available cash. All other accounts and margin are completely isolated.
