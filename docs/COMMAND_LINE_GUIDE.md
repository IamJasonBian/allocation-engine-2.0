# Command Line Usage Guide

## 🚀 Quick Commands

### **Dry Run (Default - Safe)**
```bash
python momentum_strategy.py
```
✅ **No real orders** - just shows what would happen

---

### **Live Trading (Real Money)**
```bash
python momentum_strategy.py --live
```
⚠️ **Executes real orders** - 5 second warning before starting

---

### **Trade Different Stock**
```bash
python momentum_strategy.py --symbol MSFT
```
Trade MSFT instead of AAPL (dry run)

```bash
python momentum_strategy.py --symbol GOOGL --live
```
Trade GOOGL with live orders

---

### **Adjust Position Size**
```bash
python momentum_strategy.py --position-size 0.05
```
Use 5% of cash instead of 10% (more conservative)

```bash
python momentum_strategy.py --position-size 0.20
```
Use 20% of cash (more aggressive)

---

### **Skip Backtest (Faster)**
```bash
python momentum_strategy.py --skip-backtest
```
Only show current signal, skip historical analysis

---

### **Combined Options**
```bash
# Trade TSLA with 15% position size, live trading
python momentum_strategy.py --symbol TSLA --position-size 0.15 --live

# Quick check on AAPL without backtest
python momentum_strategy.py --skip-backtest

# Conservative MSFT trading (5% position)
python momentum_strategy.py --symbol MSFT --position-size 0.05 --live
```

---

## 📋 All Options

| Flag | Description | Example |
|------|-------------|---------|
| `--live` | Enable live trading | `--live` |
| `--symbol TICKER` | Stock to trade | `--symbol MSFT` |
| `--position-size PCT` | Cash % per trade | `--position-size 0.05` |
| `--skip-backtest` | Skip historical analysis | `--skip-backtest` |
| `--help` | Show help | `--help` |

---

## ⚠️ Live Trading Warning

When you run with `--live`, you'll see:

```
⚠️  WARNING: LIVE TRADING MODE ENABLED
══════════════════════════════════════════════════════════════════════
Real orders will be executed if signals are generated!
Press Ctrl+C within 5 seconds to cancel...
══════════════════════════════════════════════════════════════════════
```

**Press Ctrl+C** within 5 seconds to cancel if you change your mind!

---

## 💡 Recommended Workflow

### **1. Test in Dry Run**
```bash
python momentum_strategy.py
```

### **2. Review Results**
- Check backtest signals
- Review current signal
- Verify portfolio

### **3. Enable Live (When Ready)**
```bash
python momentum_strategy.py --live
```

### **4. Monitor Results**
Check your Robinhood app or run:
```bash
python safe_cash_bot.py
```

---

## 📊 Example Sessions

### **Conservative AAPL Trading**
```bash
# Dry run with 5% position size
python momentum_strategy.py --position-size 0.05

# If satisfied, go live
python momentum_strategy.py --position-size 0.05 --live
```

### **Multiple Stocks (Portfolio)**
```bash
# Check signals for multiple stocks (dry run)
python momentum_strategy.py --symbol AAPL --skip-backtest
python momentum_strategy.py --symbol MSFT --skip-backtest
python momentum_strategy.py --symbol GOOGL --skip-backtest
```

### **Quick Check (No Backtest)**
```bash
# Fast signal check
python momentum_strategy.py --skip-backtest
```

---

## 🔄 Automation

### **Cron Job (Dry Run)**
```bash
crontab -e

# Add: Run every hour during market hours (dry run for monitoring)
30 9-16 * * 1-5 cd ~/robinhood-trading && python3 momentum_strategy.py --skip-backtest >> logs/momentum.log 2>&1
```

### **Cron Job (Live Trading)**
```bash
# ⚠️ LIVE TRADING - Use with caution!
30 9-16 * * 1-5 cd ~/robinhood-trading && python3 momentum_strategy.py --live --skip-backtest >> logs/momentum_live.log 2>&1
```

---

## 🆘 Troubleshooting

### **"No module named 'argparse'"**
Argparse is built-in to Python 3. Update Python:
```bash
python3 momentum_strategy.py
```

### **Want to stop live trading mid-run**
Press **Ctrl+C** during the 5-second countdown

### **Check if order was executed**
```bash
python safe_cash_bot.py
# Review positions and recent activity
```

---

## ✅ Safety Checklist

Before running `--live`:

- ✅ Tested in dry run mode
- ✅ Reviewed backtest results
- ✅ Understand current signal
- ✅ Verified available cash
- ✅ Comfortable with position size
- ✅ Know how to check orders in Robinhood app

---

## 📚 Related Files

- **Strategy Code**: `momentum_strategy.py`
- **Strategy Guide**: `MOMENTUM_STRATEGY_GUIDE.md`
- **Quick Start**: `QUICK_START_MOMENTUM.md`
- **Bot Guide**: `ACCOUNT_919433888_README.md`

---

## 🎯 Summary

**Dry Run (Safe)**:
```bash
python momentum_strategy.py
```

**Live Trading**:
```bash
python momentum_strategy.py --live
```

**Get Help**:
```bash
python momentum_strategy.py --help
```

That's it! Start with dry run and only use `--live` when you're comfortable.
