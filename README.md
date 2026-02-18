## Summary

Version - 0.1.0 - This is part of the [allocation-manager](https://github.com/OptimChain/allocation-manager/) system.

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

## Structure

```
robinhood-trading/
├── trading_system/       
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

**Full Guide**: [TRADING_SYSTEM_GUIDE.md](TRADING_SYSTEM_GUIDE.md)

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

## Configuration - on personal RH

### Environment Variables (.env)

Before Running -

- Never commit `.env` to git
- Enable 2FA on your Robinhood account
- Use `chmod 600 .env` for restrictive permissions
- Dry run mode by default
- Account isolation (locked to 490706777)
- Cash-only trading (no margin)
- 10-second countdown before live trading

```bash
# Robinhood Credentials
RH_AUTO_EMAIL=your_email@example.com
RH_AUTO_PASSWORD=your_password
RH_AUTOMATED_ACCOUNT_NUMBER=490706777

# Twelve Data API (for trading_system)
TWELVE_DATA_API_KEY=your_api_key_here
```

**State Management**:
- Persistent state in `trading_state.json`
- Tracks all metrics per symbol
- Records queued and active orders
- Maintains order history


### Component Scripts

This is a mess right now, working on clean-up

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

### Core Utility Scripts

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

## MISC

- **[TRADING_SYSTEM_GUIDE.md](TRADING_SYSTEM_GUIDE.md)** m
- **[PROJECT_STRUCTURE.md](PROJECT_STRUCTURE.md)** 
- **[docs/](docs/)** - Additional documentation


## Requirements

- Python 3.7+
- robin-stocks >= 3.0.0
- python-dotenv >= 1.0.0
- pyotp >= 2.9.0
- requests >= 2.31.0

Install all: `pip install -r requirements.txt`

## Workflow

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

## Limits

### API Rate Limits

**Twelve Data Free Tier**:
- 8 calls/minute
- 800 calls/day
- System uses ~12 calls per run (4 symbols × 3 calls)

**Usage Limits**:
- Continuous mode: 5+ minute intervals
- Daily limit: ~66 runs



**Version**: 2.0 (Modular Architecture)
**Last Updated**: January 2025
