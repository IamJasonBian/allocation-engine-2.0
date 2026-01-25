# Trading System Quick Start Guide

## Overview

A clean, modular 30-day breakout trading system with the following components:

- **Market Data**: Twelve Data API (BTC, SPY, QQQ, AMZN)
- **Strategy**: Buy at 30-day low, Sell at 30-day high
- **Execution**: Robinhood (account 490706777)
- **State**: Persistent JSON storage for metrics and orders

## Quick Start

### 1. Test the System (Dry Run)

```bash
# Single run - test everything
python -m trading_system.main

# This will:
# ✓ Fetch market data for BTC, SPY, QQQ, AMZN
# ✓ Calculate intraday volatility, highs/lows
# ✓ Calculate 30-day highs/lows
# ✓ Generate trading signals
# ✓ Simulate orders (NO real execution)
# ✓ Save state to trading_state.json
```

### 2. Run Live (Real Trading)

```bash
# Live trading - ONE TIME execution
python -m trading_system.main --live

# WARNING: This executes REAL orders!
# You get a 10-second countdown to cancel
```

### 3. Run Continuously

```bash
# Check every 5 minutes (dry run)
python -m trading_system.main --continuous

# Check every 10 minutes (LIVE)
python -m trading_system.main --live --continuous --interval 10
```

## What Gets Tracked

For each symbol (BTC, SPY, QQQ, AMZN):

### Metrics (Updated Every Run)
- Current price
- Intraday high/low
- Intraday volatility (annualized %)
- 30-day high (highest price in last 30 days)
- 30-day low (lowest price in last 30 days)

### Orders (Queued & Active)
- **Buy Orders**: Queued when price hits 30-day low
- **Sell Orders**: Queued when price hits 30-day high
- **Status**: queued → placed → filled/cancelled

All state is saved to `trading_state.json`

## Trading Logic

```
IF current_price <= 30-day low AND no position:
    → Queue BUY order (market order)
    → Position size = 25% of available cash

IF current_price >= 30-day high AND have position:
    → Queue SELL order (market order)
    → Sell entire position

OTHERWISE:
    → HOLD (no action)
```

## System Architecture

```
trading_system/
│
├── main.py                          # Orchestrator - run this
│   ├── Fetches data from Twelve Data
│   ├── Calculates metrics
│   ├── Executes strategy
│   └── Places orders via SafeCashBot
│
├── data_providers/
│   └── twelve_data.py              # Market data API
│       ├── get_quote()             # Current price
│       ├── get_intraday_data()     # 5-min intervals
│       └── get_daily_data()        # 30-day history
│
├── strategies/
│   └── breakout_strategy.py        # 30-day breakout logic
│       ├── analyze_symbol()        # Generate signals
│       └── calculate_position_size()
│
├── state/
│   └── state_manager.py            # State persistence
│       ├── update_metrics()        # Save metrics
│       ├── queue_buy_order()       # Queue buy
│       ├── queue_sell_order()      # Queue sell
│       └── print_state_summary()   # View state
│
└── utils/
    └── metrics.py                   # Metrics calculation
        ├── calculate_intraday_volatility()
        ├── calculate_intraday_range()
        └── calculate_30day_range()
```

## Testing Components

Test each module individually:

```bash
# Test Twelve Data API
python trading_system/data_providers/twelve_data.py

# Test metrics calculation
python trading_system/utils/metrics.py

# Test breakout strategy
python trading_system/strategies/breakout_strategy.py

# Test state management
python trading_system/state/state_manager.py
```

## Monitoring

### View Current State
```python
from trading_system.state.state_manager import StateManager
state = StateManager()
state.print_state_summary()
```

### Check Metrics for a Symbol
```python
metrics = state.get_metrics('BTC')
print(f"BTC Price: ${metrics['current_price']}")
print(f"30D High: ${metrics['30d_high']}")
print(f"30D Low: ${metrics['30d_low']}")
```

### View Active Orders
```python
orders = state.get_active_orders('BTC')
print(f"Buy order: {orders['buy']}")
print(f"Sell order: {orders['sell']}")
```

### Check Order History
```python
history = state.get_order_history('BTC', limit=5)
for order in history:
    print(f"{order['type']} - {order['status']} - {order['queued_at']}")
```

## Configuration

All configuration is in `.env`:

```bash
# Twelve Data API (already configured)
TWELVE_DATA_API_KEY=f2c57fbb0a794024b0defff74af45686

# Robinhood (already configured)
RH_AUTOMATED_ACCOUNT_NUMBER=490706777
RH_AUTO_EMAIL=jasonzb@umich.edu
RH_AUTO_PASSWORD=Audible73!
```

## Example Output

```
======================================================================
RUNNING TRADING SYSTEM
======================================================================
Time: 2024-01-24 10:30:00
Symbols: BTC, SPY, QQQ, AMZN
======================================================================

Processing BTC...

======================================================================
METRICS: BTC
======================================================================
Current Price:        $42,000.00
Intraday High:        $43,000.00
Intraday Low:         $41,000.00
Intraday Volatility:  2.50%
30-Day High:          $45,000.00
30-Day Low:           $38,000.00
======================================================================

======================================================================
SIGNAL ANALYSIS: BTC
======================================================================
Signal: BUY_AT_LOW
Reason: Price $38,050.00 at 30-day low $38,000.00

Order Details:
  Action: BUY
  Type: market
  Current Price: $38,050.00
  Trigger Price: $38,000.00
======================================================================

======================================================================
EXECUTING BUY ORDER: BTC
======================================================================
Quantity: 0.0658
Price: $38,050.00
Total: $2,504.29
Mode: DRY RUN
======================================================================
```

## API Rate Limits

**Twelve Data Free Tier**:
- 8 calls/minute
- 800 calls/day

**System Usage**:
- 3 calls per symbol per run
- 4 symbols = 12 calls per run
- Can run ~66 times per day

**Safe Intervals**:
- Continuous mode: Every 5+ minutes
- Daily limit: ~66 runs

## Safety Checklist

Before running `--live`:

- [ ] Tested in dry run mode (`python -m trading_system.main`)
- [ ] Verified state file looks correct (`trading_state.json`)
- [ ] Checked account balance on Robinhood
- [ ] Confirmed account 490706777 is active
- [ ] Understand you have 10 seconds to cancel
- [ ] Know you can press Ctrl+C to stop continuous mode

## Common Issues

**"No data returned from Twelve Data"**
- Check internet connection
- Verify API key in `.env`
- Check rate limits (8/min, 800/day)

**"Insufficient cash to buy"**
- Check Robinhood account balance
- Verify tradeable cash available
- Each position is 25% of cash

**"Order failed to execute"**
- Ensure not in dry run mode
- Check Robinhood account is active
- Verify account 490706777 exists

**State file corrupted**
- Delete `trading_state.json`
- System will create new one on next run

## Files Created

```
trading_system/
├── __init__.py
├── README.md
├── main.py
├── data_providers/
│   ├── __init__.py
│   └── twelve_data.py
├── strategies/
│   ├── __init__.py
│   └── breakout_strategy.py
├── state/
│   ├── __init__.py
│   └── state_manager.py
└── utils/
    ├── __init__.py
    └── metrics.py

trading_state.json          # Created on first run
requirements.txt            # Updated with requests
.env                        # Updated with Twelve Data key
```

## Next Steps

1. **Test in dry run**: `python -m trading_system.main`
2. **Review state file**: Open `trading_state.json`
3. **Monitor for signals**: Run a few times to see signal generation
4. **Go live when ready**: `python -m trading_system.main --live`
5. **Set up continuous**: `python -m trading_system.main --live --continuous`

## Support

For issues or questions:
- Check `trading_system/README.md` for detailed docs
- Review component test outputs
- Inspect `trading_state.json` for current state
- Check Robinhood account for order status
