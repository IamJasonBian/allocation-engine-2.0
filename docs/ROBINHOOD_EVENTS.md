# Robinhood events: explaining the largest per-day changes

A scoping note. **Nothing here is implemented** — this is the inventory, the
gap, the proposed shape, and the open questions that need sign-off first.

## What this is trying to answer

> On the day the book moved the most, *what moved it?*

Today the pipeline cannot answer either half. It cannot rank days by how much
the book moved, and for the days it could rank, it cannot attribute the move to
a cause. Both gaps have the same root: the engine only ever reads **orders**.

## The name collision — read this first

"Events" already means something in this repo, and it is not what Robinhood
means by it.

| | repo-local "order events" | Robinhood "events" |
|---|---|---|
| Where | `OrderEvent` in `app/background.py:21`, flushed by `app/s3_store.py:51` | `https://api.robinhood.com/options/events/` |
| Served at | `GET /api/events` (`app/api/events.py:12`) | *(not read anywhere)* |
| Contains | normalised stock + option **orders** | assignment, exercise, expiration |

`GET /api/events` is taken. Anything new needs its own namespace — see
[Where it lands](#where-it-would-land).

## The gap

Every P&L path in the repo is **fill-derived**. `compute_pnl`
(`app/pnl.py:1`) replays `trade_fills()`, which is built in
`RobinhoodTrader._fetch_live_trade_fills` (`app/brokers/robinhood_client.py:280`)
from exactly two sources: `rh.orders.get_all_stock_orders()` and
`rh.orders.get_all_crypto_orders()`. A fill needs `cumulative_quantity > 0` and
an `average_price` (`robinhood_client.py:316`), so anything without an order
row is structurally invisible.

Non-order events move position and cash with **no order and no fill**:

- **Option assignment** — short put assigned: shares appear at the strike, cash
  leaves. No stock order exists.
- **Option exercise** — same shape, opposite trigger.
- **Expiration worthless** — a short option position vanishes; the premium
  collected at open becomes realized. No closing order.
- **Dividend** — cash arrives against a held position. No order.
- **Split** — quantity and average price both change. No order.
- **Interest / stock-loan / margin interest** — cash moves against no position
  at all.

This is not an edge case for this book. On a wheel strategy, **assignment is
the single largest one-day change there is** — the full notional of the
contract moves in one settlement — and it is precisely the event the current
pipeline cannot see. `post_positions` is a whole-book replace
(`app/trading_db.py:66`), so the assigned shares *do* appear in the dashboard
at the next 900s sync; they appear with no row anywhere explaining where they
came from.

### There is also no daily baseline

Ranking days needs a per-day equity series. Two candidates exist and neither is
read:

- `RobinhoodTrader.account()` (`robinhood_client.py:121`) calls
  `rh.profiles.load_portfolio_profile()` and keeps `equity` and `market_value`.
  That response also carries `equity_previous_close`,
  `adjusted_equity_previous_close`, and `portfolio_equity_previous_close` —
  all dropped on the floor. One of these is the day's denominator.
- `rh.account.get_historical_portfolio(interval='day', span='year')` returns the
  daily equity series directly. Never called anywhere in the repo.

## Data inventory

Everything below is reachable through the already-injected box token — the
client sets `Authorization` on robin_stocks' session in `_box_auth`
(`robinhood_client.py:77`), so **no new auth path and no RH login is
involved**. All of it is read-only.

| Feed | Accessor | Explains |
|---|---|---|
| Option events | `rh.stocks.get_events(symbol)` → `/options/events/` | assignment, exercise, expiration |
| Dividends | `rh.account.get_dividends()` | cash in against a position |
| Splits | `rh.stocks.get_splits(symbol)` | quantity + basis change |
| Interest (sweeps) | `rh.account.get_interest_payments()` | cash in, no position |
| Stock loan | `rh.account.get_stock_loan_payments()` | cash in, no position |
| Margin interest | `rh.account.get_margin_interest()` | cash out, no position |
| ACH / wire transfers | `rh.account.get_bank_transfers()`, `get_wire_transfers()` | **not** performance — must be netted out |
| Daily equity series | `rh.account.get_historical_portfolio()` | the per-day denominator |

Two mechanical traps in that table:

1. **`get_events` is per-symbol, not per-book.** It sends
   `{'equity_instrument_id': id_for_stock(symbol)}` and requires a ticker. A
   whole-book daily sweep needs either an iteration over the option book's
   chain symbols (N calls, N instrument lookups) or one unfiltered
   `request_get('https://api.robinhood.com/options/events/', 'pagination')`.
   The unfiltered call is one request instead of N and is the recommendation.
2. **`get_events` does not paginate.** It passes `dataType='results'`, which
   returns only the first page and ignores `next`. Any sweep built on it must
   use `'pagination'` or it will silently truncate history.

## Defining "largest per-day change"

This needs a decision before anything is built, because the obvious metric is
wrong in a specific way.

- **Δ equity day-over-day** is the honest headline number, but it counts
  deposits and withdrawals as performance. A $10k transfer would top the
  ranking on a day nothing happened.
- **Recommendation:** rank days by Δ equity **net of transfers**, then attach
  the day's events as explanation rows ranked by their own cash + position
  impact. The headline stays a real number; the events say why.

Attribution will not close exactly, and the doc should say so rather than
pretend: marks move intraday for reasons no event describes. Events explain the
*discrete* moves; the residual is market drift.

## Where it would land

Following the existing seams, not new ones:

- **Read** — a new read-only method on `RobinhoodTrader`, alongside
  `options_positions()` / `options_orders()`. Not a change to `trade_fills()`:
  `compute_pnl` assumes fills, and feeding it synthesised fills would corrupt
  average-cost basis in a way that is hard to unpick later.
- **Write** — a new `post_account_events` in `app/trading_db.py`, matching the
  append-only, de-duped shape of `post_bot_activity` (`trading_db.py:95`).
  Needs a new Trading DB table; today's tables are `stock_orders`,
  `option_orders`, `positions`, `option_positions`, `bot_activity`
  (`docs/trading-db-diagram.html`) and none of them fit.
- **Serve** — `/api/account-events`, **not** `/api/events` (taken, see above).
- **Cadence** — these settle daily, not every 900s. A daily sweep, not the
  `TRADING_DB_SYNC_SECONDS` tick. First sync pulls full paginated history;
  after that, incremental by date.
- **Gating** — ungated on `DRY_RUN`, for the same reason `post_positions` and
  `post_orders` are (`CLAUDE.md`): a dry-run engine still reads the real book,
  and the dashboard should reflect it.

### Boundaries this work does not cross

- Core-logic only. No `auth-service/` change, no VM change, no firewall change.
- Read-only throughout. No new order path, no new mutation surface.
- No RH login. Box-vended token only, via the existing `_box_auth` injection.

## Waterfall plan

### Phase 1 — Requirements

Pin the metric (Δ equity net of transfers vs. alternatives), pick the equity
source (`equity_previous_close` per tick vs. `get_historical_portfolio` daily
series), and decide which feeds are in the first cut. Assignment / exercise /
expiration and dividends carry nearly all the mass; interest and stock-loan are
rounding error on this book and can wait.

*Exit:* sign-off on the metric and the feed list.

### Phase 2 — Design

Normalised event shape covering all feeds (`event_date`, `type`, `symbol`,
`quantity`, `cash_amount`, `source_id` for de-dup). Trading DB table + de-dup
key. Method signature on `RobinhoodTrader`. Backfill strategy for first sync.

*Exit:* schema and signature reviewed; confirmation that no auth-service or
guardrail change is required.

### Phase 3 — Read path

`RobinhoodTrader` method + unit tests against recorded fixtures. Verify the
unfiltered `/options/events/` call paginates and that a known past assignment
comes back.

*Exit:* method returns real events for a known assignment date; suite green.

### Phase 4 — Write + serve

`post_account_events`, daily sweep wiring, `/api/account-events`.

*Exit:* a real assignment appears in the Trading DB with the shares it created
matching the position delta already visible from `post_positions`.

### Phase 5 — Ranking

The per-day change series and its attribution rows.

*Exit:* the top-N days rank correctly against a hand-checked month, and each
day's largest event is named.

## Open questions

1. **Metric** — is "Δ equity net of transfers" the right headline, or should it
   be gross Δ equity with transfers shown as their own row?
2. **Scope of first cut** — option events + dividends only, or all feeds?
3. **History depth** — how far back does the first backfill go? Full history is
   a large paginated pull; a year is one call for the equity series.
4. **Table vs. reuse** — new `account_events` table, or extend `bot_activity`?
   `bot_activity` is de-duped on `{order_id}:{status}` and these have no
   `order_id`, which argues for a new table.
5. **Residual** — is an attribution that does not close to zero acceptable, or
   does the dashboard need an explicit "unexplained / market drift" row?
