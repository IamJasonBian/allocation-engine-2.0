# ⚠️ PERMANENT ACCOUNT CONFIGURATION ⚠️

## 🔒 LOCKED ACCOUNT: 491498655

**ALL TRADING BOTS ARE PERMANENTLY LOCKED TO THIS ACCOUNT**

---

## 📊 Account Details

| Property | Value |
|----------|-------|
| **Account Number** | **491498655** |
| **Type** | Roth IRA (Tax-free growth) |
| **Status** | Active |
| **Created** | March 15, 2025 |
| **Equity** | ~$2,000 |
| **Cash** | $379.79 |
| **Positions** | 1 (QQQ) |

---

## 🤖 Bots Using This Account

All these bots are locked to account **491498655**:

1. ✅ `safe_cash_bot.py` - Portfolio checker
2. ✅ `afterhours_daily_strategy.py` - After-hours AAPL strategy
3. ✅ `momentum_strategy.py` - Momentum trading
4. ✅ `place_single_order.py` - Single order placement
5. ✅ `verify_isolation.py` - Account verification

---

## 🛡️ Safety Verification

Each bot verifies the account number on startup:

```python
if self.account_number != "491498655":
    print(f"⚠️ WARNING: Expected account 491498655")
    # Requires confirmation to continue
```

---

## 📋 Your Other Accounts (NOT Used by Bots)

| Account | Type | Equity | Notes |
|---------|------|--------|-------|
| 5RX91619 | Individual (Margin) | $105,705 | Main account - NOT automated |
| 491498655 | **Roth IRA** | **$1,943** | **← AUTOMATED TRADING** |
| 919433888 | Traditional IRA | $46,458 | NOT automated |
| 311171450745 | Crypto | Unknown | Crypto only |

---

## ⚠️ DO NOT CHANGE THIS ACCOUNT

This configuration is **permanent** and should **NOT be changed** unless you explicitly want to switch to a different account.

### To Change Account (Not Recommended)

If you absolutely must change accounts:

1. Edit `.env` file:
   ```bash
   RH_AUTOMATED_ACCOUNT_NUMBER=NEW_ACCOUNT_NUMBER
   ```

2. Update verification in each bot:
   ```bash
   # In safe_cash_bot.py, verify_isolation.py, etc.
   if self.account_number != "NEW_ACCOUNT_NUMBER":
   ```

3. Test thoroughly before going live

---

## ✅ Verification Commands

Check the account is properly configured:

```bash
# Verify isolation
python verify_isolation.py

# Check portfolio
python safe_cash_bot.py

# Test after-hours strategy
python afterhours_daily_strategy.py
```

Expected output:
```
🔒 LOCKED TO ACCOUNT: 491498655
Type: cash (Roth IRA)
```

---

## 📝 Account Characteristics

### Advantages
✅ **Tax-free growth** (Roth IRA)
✅ **Cash only** (no margin risk)
✅ **API verified** working
✅ **Safe for automation**

### Limitations
⚠️ **$379.79 cash** (limited buying power)
⚠️ **$7,000/year** contribution limit (IRS)
⚠️ **No margin trading** allowed
⚠️ **Early withdrawal penalties** (before age 59½)

---

## 🎯 Strategy Recommendations

With **$379.79 cash** available:

| Strategy | Suitable? | Notes |
|----------|-----------|-------|
| After-hours daily | ✅ Yes | 1 share AAPL (~$249) |
| Momentum | ⚠️ Limited | Need 50%+ position size |
| Single orders | ✅ Yes | Good for learning |
| High-frequency | ❌ No | Insufficient capital |

---

## 🔐 Security

- Account number stored in `.env` file
- `.env` is in `.gitignore` (never committed)
- All bots verify account on startup
- Dry run mode by default

---

**Last Updated**: January 23, 2026
**Account**: 491498655 (Roth IRA)
**Status**: ✅ Permanently Locked
