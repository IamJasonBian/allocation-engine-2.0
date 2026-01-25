# Trading System - 30-Day Breakout Strategy

A clean, modular trading system that implements a 30-day high/low breakout strategy using Twelve Data for market data and Robinhood for order execution.

## Architecture

```
trading_system/
├── main.py                      # Main orchestrator
├── data_providers/
│   └── twelve_data.py          # Twelve Data API integration
├── strategies/
│   └── breakout_strategy.py    # 30-day breakout strategy
├── state/
│   └── state_manager.py        # State persistence and management
└── utils/
    └── metrics.py              # Metrics calculation utilities
```

## Strategy Overview

**30-Day High/Low Breakout Strategy**

- **Buy Signal**: Triggered when price hits 30-day low
- **Sell Signal**: Triggered when price hits 30-day high
- **Instruments**: BTC, S&P 500 (SPY), QQQ, AMZN
- **Position Size**: 25% of portfolio per symbol
- **Order Type**: Market orders

## Metrics Tracked

For each symbol:
- **Intraday Metrics**:
  - Current price
  - Intraday high/low
  - Intraday volatility (annualized)

- **30-Day Metrics**:
  - 30-day high
  - 30-day low

## State Management

The system maintains persistent state in `trading_state.json`:

```json
{
  "symbols": {
    "BTC": {
      "metrics": {
        "current_price": 42000.00,
        "intraday_high": 43000.00,
        "intraday_low": 41000.00,
        "intraday_volatility": 2.5,
        "30d_high": 45000.00,
        "30d_low": 38000.00
      },
      "orders": {
        "active_buy": {
          "type": "buy",
          "status": "queued",
          "details": {...}
        },
        "active_sell": null,
        "order_history": [...]
      },
      "last_signal": "BUY_AT_LOW",
      "last_updated": "2024-01-24T10:30:00"
    }
  }
}
```

## Installation

1. Install dependencies:
```bash
pip install -r requirements.txt
```

2. Configure environment variables in `.env`:
```bash
# Already configured:
TWELVE_DATA_API_KEY=f2c57fbb0a794024b0defff74af45686
RH_AUTOMATED_ACCOUNT_NUMBER=490706777
```

## Usage

### Single Run (Dry Run)
```bash
# From project root
python -m trading_system.main
```

### Single Run (Live Trading)
```bash
python -m trading_system.main --live
```

### Continuous Mode (Checks every 5 minutes)
```bash
# Dry run
python -m trading_system.main --continuous

# Live trading, check every 10 minutes
python -m trading_system.main --live --continuous --interval 10
```

### Test Individual Components

Test Twelve Data integration:
```bash
python trading_system/data_providers/twelve_data.py
```

Test metrics calculation:
```bash
python trading_system/utils/metrics.py
```

Test strategy logic:
```bash
python trading_system/strategies/breakout_strategy.py
```

Test state management:
```bash
python trading_system/state/state_manager.py
```

## How It Works

### 1. Data Collection
- Fetches intraday data (5-min intervals) from Twelve Data
- Fetches 30-day daily data for high/low calculation
- Retrieves real-time quotes for current price

### 2. Metrics Calculation
- Calculates intraday volatility using standard deviation of returns
- Determines intraday high/low from recent data
- Calculates 30-day high/low from daily data

### 3. Strategy Execution
- Analyzes current price vs 30-day range
- Generates BUY signal when price ≤ 30-day low
- Generates SELL signal when price ≥ 30-day high
- Checks existing positions to avoid duplicate orders

### 4. Order Management
- Queues orders in state before execution
- Calculates position size (25% of available cash per symbol)
- Executes market orders via Robinhood
- Tracks order status (queued → placed → filled)

### 5. State Persistence
- Saves all metrics to `trading_state.json`
- Records queued and active orders
- Maintains order history
- Tracks last trading signal per symbol

## Example Output

```
======================================================================
RUNNING TRADING SYSTEM
======================================================================
Time: 2024-01-24 10:30:00
Symbols: BTC, SPY, QQQ, AMZN
======================================================================

######################################################################
Processing BTC
######################################################################

Fetching market data for BTC...

======================================================================
METRICS: BTC
======================================================================
Current Price:        $42,000.00

Intraday Range:
  High:               $43,000.00
  Low:                $41,000.00
  Volatility:         2.50%

30-Day Range:
  30-Day High:        $45,000.00
  30-Day Low:         $38,000.00
======================================================================

======================================================================
SIGNAL ANALYSIS: BTC
======================================================================
Signal: HOLD
Reason: No position. Price $42,000.00 is 10.5% above 30d low $38,000.00
======================================================================
```

## API Rate Limits

**Twelve Data Free Tier**:
- 8 API calls/minute
- 800 API calls/day

With 4 symbols and 3 API calls per symbol (quote, intraday, daily), each run uses 12 API calls.

**Recommended intervals**:
- Single runs: No restriction
- Continuous mode: 5+ minutes between runs

## Safety Features

1. **Dry Run Mode**: Default mode simulates all orders
2. **State Persistence**: All decisions are logged
3. **Position Tracking**: Prevents duplicate orders
4. **Account Isolation**: Locked to account 490706777
5. **Live Trading Warning**: 10-second countdown before execution

## Monitoring

View current state:
```python
from trading_system.state.state_manager import StateManager
state = StateManager()
state.print_state_summary()
```

Check order history for a symbol:
```python
history = state.get_order_history('BTC', limit=10)
for order in history:
    print(order)
```

## Troubleshooting

**No data returned from Twelve Data**:
- Check API key is correct
- Verify rate limits not exceeded
- Check symbol format (BTC/USD vs BTC)

**Orders not executing**:
- Verify not in dry run mode
- Check sufficient cash available
- Ensure account 490706777 is active

**State file corruption**:
- Delete `trading_state.json` to reset
- System will create new state file

## Future Enhancements

- Add email/SMS notifications for signals
- Implement stop-loss protection
- Add backtesting with historical data
- Support for additional data providers
- Web dashboard for monitoring
- Advanced position sizing strategies
