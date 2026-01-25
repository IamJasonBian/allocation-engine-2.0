# Robinhood Trading System

A professional, modular trading system for Robinhood with multiple strategies and clean architecture.

## 🚀 Quick Start

### New Users: Start Here

1. **Install dependencies**:
```bash
pip install -r requirements.txt
```

2. **Configure credentials**:
```bash
python scripts/setup_credentials.py
```

3. **Run the trading system** (dry run):
```bash
python -m trading_system.main
```

4. **Go live** when ready:
```bash
python -m trading_system.main --live
```

## 📁 Project Structure

```
robinhood-trading/
├── trading_system/        # ⭐ NEW: Production trading system
│   ├── main.py           # 30-day breakout strategy
│   ├── data_providers/   # Market data (Twelve Data API)
│   ├── strategies/       # Trading strategies
│   ├── state/            # State management
│   └── utils/            # Metrics calculations
│
├── utils/                 # Core utilities
│   ├── rh_auth.py        # Robinhood authentication
│   └── safe_cash_bot.py  # Cash trading bot
│
├── old_strategies/        # Legacy strategies (reference)
├── scripts/               # Utility scripts
└── docs/                  # Additional documentation
```

For detailed structure, see [PROJECT_STRUCTURE.md](PROJECT_STRUCTURE.md)

## 🎯 Main Features

### Trading System (NEW - Recommended)

**30-Day Breakout Strategy**:
- Buy when price hits 30-day low
- Sell when price hits 30-day high
- Tracks: BTC, S&P 500 (SPY), QQQ, AMZN
- Persistent state management
- Market data from Twelve Data API

**Usage**:
```bash
# Single run (dry run)
python -m trading_system.main

# Live trading
python -m trading_system.main --live

# Continuous mode (every 5 minutes)
python -m trading_system.main --continuous

# Custom interval (every 10 minutes)
python -m trading_system.main --continuous --interval 10
```

📖 **Full Guide**: [TRADING_SYSTEM_GUIDE.md](TRADING_SYSTEM_GUIDE.md)

### Utility Scripts

```bash
# Setup credentials
python scripts/setup_credentials.py

# List accounts
python scripts/list_accounts.py

# Place single order
python scripts/place_single_order.py

# Verify account isolation
python scripts/verify_isolation.py
```

### Legacy Strategies

```bash
# Momentum strategy (moving averages)
python old_strategies/momentum_strategy.py

# After-hours strategy
python old_strategies/afterhours_daily_strategy.py
```

## ⚙️ Configuration

### Environment Variables (.env)

```bash
# Robinhood Credentials
RH_AUTO_EMAIL=your_email@example.com
RH_AUTO_PASSWORD=your_password
RH_AUTOMATED_ACCOUNT_NUMBER=490706777

# Twelve Data API (for trading_system)
TWELVE_DATA_API_KEY=your_api_key_here
```

**Security Notes**:
- Never commit `.env` to git
- Enable 2FA on your Robinhood account
- Use `chmod 600 .env` for restrictive permissions

## 📊 Trading System Features

✅ **Market Data Integration**:
- Real-time quotes from Twelve Data
- Intraday data (5-minute intervals)
- 30-day historical data

✅ **Metrics Tracking**:
- Intraday volatility
- Intraday high/low
- 30-day high/low
- Current price

✅ **State Management**:
- Persistent state in `trading_state.json`
- Tracks all metrics per symbol
- Records queued and active orders
- Maintains order history

✅ **Safety Features**:
- Dry run mode by default
- Account isolation (locked to 490706777)
- Cash-only trading (no margin)
- 10-second countdown before live trading

## 🔧 Development

### Test Individual Components

```bash
# Test Twelve Data API
python trading_system/data_providers/twelve_data.py

# Test metrics calculation
python trading_system/utils/metrics.py

# Test strategy logic
python trading_system/strategies/breakout_strategy.py

# Test state management
python trading_system/state/state_manager.py
```

### Use Core Utilities

```python
from utils.rh_auth import RobinhoodAuth
from utils.safe_cash_bot import SafeCashBot
import robin_stocks.robinhood as r

# Login
auth = RobinhoodAuth()
auth.login('automated')

# Use safe cash bot
bot = SafeCashBot()
bot.get_portfolio_summary()

# Logout
auth.logout()
```

## 📚 Documentation

- **[TRADING_SYSTEM_GUIDE.md](TRADING_SYSTEM_GUIDE.md)** - Quick start guide for the trading system
- **[PROJECT_STRUCTURE.md](PROJECT_STRUCTURE.md)** - Complete project organization
- **[docs/](docs/)** - Additional documentation and guides

### Key Docs in `docs/`:
- Strategy guides (Momentum, After-hours)
- Account configuration
- Command line usage
- Setup completion guides

## 🛠️ System Requirements

- Python 3.7+
- robin-stocks >= 3.0.0
- python-dotenv >= 1.0.0
- pyotp >= 2.9.0
- requests >= 2.31.0

Install all: `pip install -r requirements.txt`

## 📈 Example Workflow

1. **Setup** (one-time):
```bash
pip install -r requirements.txt
python scripts/setup_credentials.py
```

2. **Test in dry run**:
```bash
python -m trading_system.main
```

3. **Review state**:
```bash
cat trading_state.json
```

4. **Go live**:
```bash
python -m trading_system.main --live
```

5. **Run continuously**:
```bash
python -m trading_system.main --live --continuous
```

## ⚠️ Important Notes

### Account Configuration
- **Locked Account**: System uses account 490706777
- **Cash Only**: No margin trading
- **Position Size**: 25% of available cash per symbol

### API Rate Limits
**Twelve Data Free Tier**:
- 8 calls/minute
- 800 calls/day
- System uses ~12 calls per run (4 symbols × 3 calls)

**Safe Usage**:
- Continuous mode: 5+ minute intervals
- Daily limit: ~66 runs

### Trading Hours
Market data updates during trading hours:
- Stock markets: 9:30 AM - 4:00 PM ET
- Crypto (BTC): 24/7

## 🐛 Troubleshooting

**"No module named 'safe_cash_bot'"**:
- File was moved to `utils/`. Imports have been updated.
- Run from project root: `python -m trading_system.main`

**"No data from Twelve Data"**:
- Check API key in `.env`
- Verify rate limits (8/min, 800/day)
- Check internet connection

**Orders not executing**:
- Verify not in dry run mode
- Check account 490706777 is active
- Ensure sufficient cash available

**State file issues**:
- Delete `trading_state.json` to reset
- System creates new file automatically

## 📜 Disclaimer

**This is for educational purposes only. Trading involves risk of loss. Use at your own risk.**

- Always test with small amounts first
- Review all orders before execution
- Keep track of trades for tax purposes
- Follow Robinhood's Terms of Service
- Past performance does not guarantee future results

## 🤝 Support

For questions or issues:
1. Check the documentation guides
2. Review `PROJECT_STRUCTURE.md` for file organization
3. Test individual components with their test functions
4. Review `trading_state.json` for current system state

---

**Version**: 2.0 (Modular Architecture)
**Last Updated**: January 2025
