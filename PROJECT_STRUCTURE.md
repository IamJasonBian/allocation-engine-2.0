# Project Structure

This document describes the organization of the Robinhood Trading project.

## Directory Structure

```
robinhood-trading/
├── trading_system/              # NEW: Clean, modular trading system
│   ├── main.py                 # Main orchestrator for 30-day breakout strategy
│   ├── data_providers/         # Market data integrations
│   │   └── twelve_data.py     # Twelve Data API client
│   ├── strategies/             # Trading strategies
│   │   └── breakout_strategy.py  # 30-day high/low breakout
│   ├── state/                  # State management
│   │   └── state_manager.py   # Persistent state storage
│   └── utils/                  # Utilities
│       └── metrics.py         # Metrics calculations
│
├── utils/                      # Core utilities (used by everything)
│   ├── rh_auth.py             # Robinhood authentication manager
│   └── safe_cash_bot.py       # Safe cash-only trading bot
│
├── old_strategies/             # Legacy/old trading strategies
│   ├── trading_bot.py         # Original example trading bot
│   ├── momentum_strategy.py   # Moving average momentum strategy
│   └── afterhours_daily_strategy.py  # After-hours trading strategy
│
├── scripts/                    # Utility scripts
│   ├── setup_credentials.py   # Interactive credential setup
│   ├── list_accounts.py       # List configured accounts
│   ├── list_profiles.py       # List Robinhood profiles
│   ├── manage_profiles.py     # Manage multiple profiles
│   ├── place_single_order.py  # Place a single order
│   ├── debug_order.py         # Debug order placement
│   ├── test_all_accounts.py   # Test account access
│   └── verify_isolation.py    # Verify account isolation
│
├── .env                        # Environment variables (credentials)
├── requirements.txt            # Python dependencies
├── accounts_config.json        # Account configuration
├── trading_state.json          # Trading system state (created on first run)
│
└── Documentation files:
    ├── README.md               # Main project README
    ├── PROJECT_STRUCTURE.md    # This file
    ├── TRADING_SYSTEM_GUIDE.md # Quick start guide for trading system
    └── trading_system/README.md # Detailed trading system docs
```

## Main Components

### 1. Trading System (NEW - Recommended)

**Location**: `trading_system/`

This is the new, clean, modular trading system with proper architecture:

- **Purpose**: Production-ready 30-day breakout trading strategy
- **Features**:
  - Market data from Twelve Data API
  - Automatic metrics calculation
  - Persistent state management
  - Buy at 30-day low, sell at 30-day high
  - Supports: BTC, S&P 500 (SPY), QQQ, AMZN

**How to run**:
```bash
# Dry run
python -m trading_system.main

# Live trading
python -m trading_system.main --live

# Continuous mode
python -m trading_system.main --continuous --interval 5
```

### 2. Core Utilities

**Location**: `utils/`

Shared utilities used by all trading strategies:

- **`rh_auth.py`**: Robinhood authentication manager
  - Handles multiple accounts (main, automated)
  - Manages login/logout
  - Token caching

- **`safe_cash_bot.py`**: Safe cash-only trading bot
  - Locked to account 490706777
  - Cash-only trading (no margin)
  - Portfolio management
  - Order execution

**Import from**:
- Trading system: `from utils.rh_auth import RobinhoodAuth`
- Scripts: Add path then `from utils.rh_auth import RobinhoodAuth`

### 3. Old Strategies

**Location**: `old_strategies/`

Legacy trading strategies for reference:

- **`trading_bot.py`**: Simple example bot
- **`momentum_strategy.py`**: Moving average crossover strategy
- **`afterhours_daily_strategy.py`**: Buy at close, sell at open

**Note**: These are legacy files kept for reference. The new `trading_system/` is recommended for production use.

**How to run**:
```bash
# From root directory
python old_strategies/momentum_strategy.py
python old_strategies/afterhours_daily_strategy.py
```

### 4. Utility Scripts

**Location**: `scripts/`

Helper scripts for account management and testing:

| Script | Purpose |
|--------|---------|
| `setup_credentials.py` | Interactive credential configuration |
| `list_accounts.py` | View all configured accounts |
| `list_profiles.py` | View Robinhood profiles |
| `manage_profiles.py` | Switch between profiles |
| `place_single_order.py` | Manual order placement |
| `debug_order.py` | Debug order issues |
| `test_all_accounts.py` | Test account access |
| `verify_isolation.py` | Verify account isolation |

**How to run**:
```bash
# From root directory
python scripts/list_accounts.py
python scripts/setup_credentials.py
python scripts/place_single_order.py
```

## Configuration Files

### `.env`
Contains all credentials and API keys:
```bash
# Robinhood credentials
RH_AUTO_EMAIL=...
RH_AUTO_PASSWORD=...
RH_AUTOMATED_ACCOUNT_NUMBER=490706777

# Twelve Data API
TWELVE_DATA_API_KEY=...
```

**⚠️ Never commit this file to git!**

### `accounts_config.json`
Account-specific configuration (if needed)

### `trading_state.json`
Created automatically by `trading_system/main.py`. Contains:
- Metrics for each symbol
- Queued and active orders
- Order history
- Last trading signals

## Import Patterns

### From trading_system modules:
```python
# Direct imports within trading_system
from trading_system.data_providers.twelve_data import TwelveDataProvider
from trading_system.state.state_manager import StateManager

# Utils from parent
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from utils.safe_cash_bot import SafeCashBot
```

### From scripts or old_strategies:
```python
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from utils.rh_auth import RobinhoodAuth
from utils.safe_cash_bot import SafeCashBot
```

### From utils (within utils folder):
```python
# Use relative imports
from .rh_auth import RobinhoodAuth
```

## Usage Guide

### For New Projects: Use `trading_system/`

1. Read `TRADING_SYSTEM_GUIDE.md`
2. Configure `.env` with your credentials
3. Test: `python -m trading_system.main`
4. Go live: `python -m trading_system.main --live`

### For Account Management: Use `scripts/`

1. Setup: `python scripts/setup_credentials.py`
2. Verify: `python scripts/list_accounts.py`
3. Test: `python scripts/verify_isolation.py`

### For Legacy Strategies: Use `old_strategies/`

These are reference implementations. Study them to understand different approaches, but use `trading_system/` for production.

## Migration Notes

All files have been reorganized from the root directory:

**Before**:
```
robinhood-trading/
├── rh_auth.py
├── safe_cash_bot.py
├── momentum_strategy.py
├── afterhours_daily_strategy.py
├── trading_bot.py
├── list_accounts.py
├── setup_credentials.py
└── ... (13 Python files in root)
```

**After**:
```
robinhood-trading/
├── trading_system/     # NEW: Clean modular system
├── utils/              # Core utilities
├── old_strategies/     # Legacy strategies
├── scripts/            # Helper scripts
└── .env, requirements.txt, etc.
```

All import paths have been updated to work with the new structure.

## Quick Reference

| Task | Command |
|------|---------|
| Run new trading system | `python -m trading_system.main` |
| Test individual components | `python trading_system/data_providers/twelve_data.py` |
| Setup credentials | `python scripts/setup_credentials.py` |
| List accounts | `python scripts/list_accounts.py` |
| Place single order | `python scripts/place_single_order.py` |
| Run old momentum strategy | `python old_strategies/momentum_strategy.py` |
| View trading state | `cat trading_state.json` |

## Next Steps

1. **Start with the trading system**: `python -m trading_system.main`
2. **Read the guide**: `TRADING_SYSTEM_GUIDE.md`
3. **Test components individually**: Each module has a test function at the bottom
4. **Go live when ready**: Add `--live` flag

All files are organized, imports are updated, and ready to use!
