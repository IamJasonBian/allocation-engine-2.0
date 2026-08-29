# Interactive Brokers (IB Gateway) Setup

The IBKR broker places **live options orders, always as Pegged-to-Stock (`PEG STK`)**, with an
optional **conditional cancel** (cancel the working order if the underlying trades above/below a
threshold). It talks to a persistent **IB Gateway** over the TWS socket using the
[`ib_async`](https://github.com/ib-api-reloaded/ib_async) library.

## Why IB Gateway (and not the Client Portal Web API)
`PEG STK` and native `PriceCondition` + `conditionsCancelOrder` (cancel-on-price) are **only
available on the TWS/IB Gateway API** — the Client Portal Web API does not offer them. Pegged-to-stock
is a managed product requirement here, so we run a real Gateway process. The tradeoff: unlike the
headless Robinhood pickle flow, this requires a **persistent Gateway service** to be up during RTH.

## Architecture
```
allocation-engine worker / api
   └─ ib_async (TWS socket) ──▶ IB Gateway (dockerized, gnzsnz/ib-gateway)
                                  ports: 4001 live / 4002 paper
```
The Gateway runs as a **separate private Render service** (`deploy/ib-gateway/`); the worker and
api reach it over Render **private networking** (`IBKR_HOST=ib-gateway`). The API port is never
exposed publicly — it holds live trading credentials.

## Secrets / environment
### On the IB Gateway service (`deploy/ib-gateway/`)
| Var | Purpose |
|---|---|
| `TWS_USERID` | IBKR username the Gateway logs in with |
| `TWS_PASSWORD` | IBKR password |
| `TRADING_MODE` | `paper` (port 4002) or `live` (port 4001) |
| `READ_ONLY_API` | `no` to allow order placement/cancel (required for trading) |
| `AUTO_RESTART_TIME` | daily IBC restart, default `23:45` ET (kept clear of the 09:30 open) |
| `TZ` | `America/New_York` |

### On the worker + api (`allocation-engine-*`)
| Var | Purpose |
|---|---|
| `IBKR_HOST` | Gateway service hostname (`ib-gateway` on Render private net) |
| `IBKR_PORT` | `4002` paper / `4001` live |
| `IBKR_CLIENT_ID` | TWS API client id (use a distinct id per connecting process) |
| `IBKR_ACCOUNT_ID` | IBKR account number |
| `IBKR_PAPER` | `true` / `false` |
| `IBKR_PEG_DELTA_DEFAULT` | fallback peg delta when none is supplied/derivable |
| `IBKR_MAX_OPTION_ORDER_QTY` | per-order contract cap |
| `IBKR_OPEN_BUFFER_MIN` | minutes after the open to start trading (default 2) |
| `IBKR_CLOSE_BUFFER_MIN` | minutes before the close to stop submitting (default 5) |
| `ENGINE_BROKER` | set to `ibkr` to route trading through IBKR |
| `DRY_RUN` | `true` = log only; `false` = place real orders |

## Pegged-to-Stock + conditional cancel behavior
- **Always `PEG STK`.** `submit_option_order` ignores any incoming `order_type` and builds a
  Pegged-to-Stock order. `startingPrice` defaults to the order `limit_price` if given, else the
  option's NBBO midpoint; `stockRefPrice` is left unset so IB pegs off the live underlying NBBO.
- **delta source (priority):** per-order `peg_delta` → the option's live IB greek delta →
  `IBKR_PEG_DELTA_DEFAULT`. The delta is signed automatically: **positive for calls, negative for puts**.
- **conditional cancel (optional, per order):** `cancel_if_underlying_above` and/or
  `cancel_if_underlying_below` attach a `PriceCondition` on the underlying with
  `conditionsCancelOrder=True`. Both set ⇒ the order is cancelled when the underlying moves
  **outside the band**.
- **PEG STK fallback:** if an exchange rejects `PEG STK`, the client falls back to a limit order at
  `startingPrice` so an order still lands (logged).

## Session handling (CRITICAL — read this)
`PEG STK` needs a live underlying NBBO and is a **DAY** order, so behavior varies across the day:
- **RTH only.** Orders are only submitted during regular trading hours, skipping a buffer at the
  open (`IBKR_OPEN_BUFFER_MIN`, auction NBBO is wide/gappy) and before the close
  (`IBKR_CLOSE_BUFFER_MIN`, thin liquidity + churn). Half-days (1:00 PM ET) are honored.
- **DAY lifecycle, re-staged each session.** Unfilled pegged orders **and their attached cancel
  guards expire at the close.** The engine re-stages desired orders each RTH session via its normal
  reconcile. **⚠️ The conditional cancel is therefore INTRADAY-ONLY — it is NOT an overnight stop.**
  Fills/positions persist; only working orders die at the close.
- **Overnight gaps.** A `PriceCondition` can fire immediately at the next open if the underlying
  gapped through the level.
- **Daily Gateway restart.** IBC restarts the Gateway once/day; scheduled to `~23:45 ET`, clear of
  the open. The client reconnects and re-syncs open orders afterward.

## Going live (paper first)
1. Deploy the Gateway in **paper**: `TRADING_MODE=paper` (port 4002). Locally:
   `cd deploy/ib-gateway && cp .env.example .env` (fill `TWS_USERID`/`TWS_PASSWORD`), then
   `docker compose up`.
2. On the worker/api set `IBKR_HOST`, `IBKR_PORT=4002`, `IBKR_PAPER=true`, `ENGINE_BROKER=ibkr`,
   and keep `DRY_RUN=true` to dry-run. Confirm `GET /api/auth/status/ibkr` shows `connected: true`.
3. Flip `DRY_RUN=false` and place a 1-lot far-OTM test order with a cancel threshold:
   ```
   curl -XPOST $API/api/options/order/ibkr -H 'Content-Type: application/json' -d '{
     "chain_symbol":"SPY","option_type":"put","strike":<far_otm>,
     "expiration":"YYYY-MM-DD","side":"BUY","quantity":1,
     "peg_delta":0.3,"cancel_if_underlying_below":<level>
   }'
   ```
   Verify in IB that the order is `PEG STK` and that moving the underlying past the level cancels it.
4. When satisfied on paper, switch the Gateway to `TRADING_MODE=live` (port 4001), set
   `IBKR_PORT=4001`, `IBKR_PAPER=false`.

## ⚠️ Operational risk — two-factor authentication
IBC's full automation assumes the Gateway login does **not** require an interactive second factor.
- Accounts on IBKR's Secure Login System that enforce **IBKEY mobile push** require a **daily
  manual approval** at Gateway login/restart — which breaks fully-unattended live trading.
- Mitigations: use a **dedicated API username** configured so the Gateway can log in headlessly, or
  run **paper** (no 2FA) until a headless live login path is confirmed.
- Schedule `AUTO_RESTART_TIME` overnight so any required approval lands outside market hours.

## References
- [gnzsnz/ib-gateway-docker](https://github.com/gnzsnz/ib-gateway-docker) (IBC + Xvfb + socat)
- [ib_async](https://github.com/ib-api-reloaded/ib_async)
- [IBKR Pegged-to-Stock](https://www.interactivebrokers.com/en/trading/orders/pegged-to-stock.php)
- [IBKR Order Types](https://www.interactivebrokers.com/campus/ibkr-api-page/order-types/)
