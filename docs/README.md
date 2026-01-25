# Additional Documentation

This directory contains supplementary documentation for the Robinhood Trading System.

## 📑 Documentation Index

### Account Configuration
- **[ACCOUNT_CONFIG.md](ACCOUNT_CONFIG.md)** - Account configuration guide
- **[FINAL_ACCOUNT_CONFIG.md](FINAL_ACCOUNT_CONFIG.md)** - Final account setup
- **[ACCOUNT_491498655_README.md](ACCOUNT_491498655_README.md)** - Account 491498655 details
- **[ACCOUNT_919433888_README.md](ACCOUNT_919433888_README.md)** - Account 919433888 details

### Strategy Guides
- **[MOMENTUM_STRATEGY_GUIDE.md](MOMENTUM_STRATEGY_GUIDE.md)** - Moving average momentum strategy
- **[AFTERHOURS_STRATEGY_GUIDE.md](AFTERHOURS_STRATEGY_GUIDE.md)** - After-hours trading strategy
- **[QUICK_START_MOMENTUM.md](QUICK_START_MOMENTUM.md)** - Quick start for momentum trading

### Usage Guides
- **[COMMAND_LINE_GUIDE.md](COMMAND_LINE_GUIDE.md)** - Command line usage
- **[PROFILE_SWITCHING_GUIDE.md](PROFILE_SWITCHING_GUIDE.md)** - Switching between accounts
- **[SETUP_COMPLETE.md](SETUP_COMPLETE.md)** - Setup completion checklist

## 🚀 Getting Started

For new users, start with the main project documentation:

1. **Main README**: `../README.md`
2. **Trading System Guide**: `../TRADING_SYSTEM_GUIDE.md`
3. **Project Structure**: `../PROJECT_STRUCTURE.md`

Then refer to the specific guides in this directory for advanced topics.

## 📚 Guide Descriptions

### Account Configuration Guides

These guides help you set up and configure multiple Robinhood accounts:

- Learn how to configure credentials
- Understand account isolation
- Set up automated trading accounts
- Manage multiple profiles

### Strategy Guides

Detailed documentation for each trading strategy:

- **Momentum Strategy**: Moving average crossover (5-day/20-day MA)
- **After-Hours Strategy**: Buy at close, sell at open
- Quick start guides for each strategy

### Usage Guides

Practical guides for using the system:

- Command line options and flags
- Switching between different accounts
- Verifying setup is complete
- Troubleshooting common issues

## 🔗 Quick Links

| Guide | Purpose | Related Files |
|-------|---------|---------------|
| Momentum Strategy | Learn moving average trading | `old_strategies/momentum_strategy.py` |
| After-Hours Strategy | Overnight trading | `old_strategies/afterhours_daily_strategy.py` |
| Account Config | Setup accounts | `scripts/setup_credentials.py` |
| Profile Switching | Manage profiles | `scripts/manage_profiles.py` |
| Command Line | CLI usage | All scripts in `scripts/` |

## 📖 Using This Documentation

1. **Browse by topic**: Use the index above to find specific guides
2. **Start with basics**: New users should read account configuration first
3. **Try examples**: Each strategy guide includes example code
4. **Reference as needed**: Keep these docs handy while developing

## 🔄 Legacy Documentation

These guides reference the old file structure. File locations have been updated:

- Old: `rh_auth.py` → New: `utils/rh_auth.py`
- Old: `safe_cash_bot.py` → New: `utils/safe_cash_bot.py`
- Old: `momentum_strategy.py` → New: `old_strategies/momentum_strategy.py`

When following these guides, adjust file paths accordingly.

## 💡 Tips

- Strategy guides are for reference - the new `trading_system/` is recommended
- Account guides are still relevant for managing credentials
- Command line guides apply to scripts in `scripts/` folder
- Check the main README for the latest recommended approach

---

**Note**: This is supplementary documentation. For the main documentation, see:
- `../README.md` - Main project README
- `../TRADING_SYSTEM_GUIDE.md` - Trading system quick start
- `../PROJECT_STRUCTURE.md` - Project organization
