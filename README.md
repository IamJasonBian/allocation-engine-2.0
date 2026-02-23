# Allocation Engine 2.0

Trade execution engine backed by **Alpaca** that reads desired orders from the [allocation-runtime-service](https://github.com/IamJasonBian/allocation-runtime-service) and reconciles them against live broker state.

## How it works

1. **Read** — Fetches desired orders from the runtime service (`/api/orders`)
2. **Reconcile** — Diffs desired state against Alpaca open orders & positions
3. **Execute** — Cancels stale orders and submits new ones via Alpaca API

## Setup

```bash
pip install -r requirements.txt
cp .env.example .env
# Fill in Alpaca keys from the 5thstreetcapital app
```

## Usage

```bash
# Check account status + desired vs actual orders
python main.py status

# Run a single reconciliation tick
python main.py once

# Run continuous loop (polls every POLL_INTERVAL_SECONDS)
python main.py run
```

## Environment variables

| Variable | Description | Default |
|----------|-------------|---------|
| `ALPACA_API_KEY` | Alpaca API key (from 5thstreetcapital) | required |
| `ALPACA_SECRET_KEY` | Alpaca secret key | required |
| `ALPACA_PAPER` | Use paper trading | `true` |
| `RUNTIME_SERVICE_URL` | Runtime service base URL | `https://route-runtime-service.netlify.app/api` |
| `POLL_INTERVAL_SECONDS` | Loop interval | `30` |
| `DRY_RUN` | Log orders without submitting | `true` |

## Architecture

```
allocation-runtime-service (read-only API)
        │
        │  GET /api/orders  (desired order set)
        ▼
┌─────────────────────┐
│  allocation-engine   │
│  2.0                 │
│                      │
│  reconcile desired   │
│  vs Alpaca state     │
└────────┬────────────┘
         │  submit / cancel
         ▼
    Alpaca Trading API
```
