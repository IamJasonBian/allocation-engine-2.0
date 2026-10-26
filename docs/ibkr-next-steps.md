# IBKR (IB Gateway) — Next Steps / Resume Guide

Status as of 2026-06-03: the IBKR **live-options-via-IB-Gateway** feature is **built and
committed across 6 PRs but not yet merged or integrated**. Integration (provisioning the
Gateway, paper/live testing) is **deferred**. This doc is the pick-up-later record.

## What was built (6 PRs, conflict-free, all off `main`)
| PR | Unit | Files |
|----|------|-------|
| #86 | `ib_async` client — **always PEG STK** + conditional cancel | `app/brokers/ibkr/*`, `tests/test_ibkr_client.py` |
| #82 | Config (Gateway vars) + `ib_async`/`pandas_market_calendars` deps | `app/config.py`, `requirements.txt` |
| #81 | Registry wiring (`ibkr` → host/port/clientId) | `app/brokers/__init__.py` |
| #85 | Engine: live option submit + RTH session gating | `app/engine.py`, `app/market_calendar.py` |
| #83 | `POST /api/options/order` (+ peg/cancel fields) | `app/api/options.py` |
| #84 | IB Gateway deployment + setup docs | `deploy/ib-gateway/*`, `docs/ibkr-setup.md`, `CLAUDE.md` |

Earlier Client-Portal-Web-API attempt (#75/#78/#79/#80) is **closed** — superseded because
`PEG STK` and native cancel-on-price are TWS/Gateway-only. PRs **#76/#77** are also superseded
by #85/#83 — close them when merging this set.

## Architecture (recap)
`runtime desired orders → engine reconcile → RTH session gate (#85) → submit when DRY_RUN=false →
IBKRTrader.submit_option_order (#86, ALWAYS PEG STK + cancel-on-cross PriceCondition) → ib_async →
IB Gateway (#84, dockerized gnzsnz/ib-gateway as a private Render service).`

## Merge plan (when resuming)
1. Merge in order: **#82 → #86 → #81 → #85 → #83 → #84** (disjoint files; `requirements.txt` only
   in #82, `ib_async` deliberately omitted from #86 to avoid a conflict).
2. Close superseded **#76** and **#77**.

## Deferred integration steps
1. **Stand up the IB Gateway** (`deploy/ib-gateway/`) as a private Render service:
   `TWS_USERID`, `TWS_PASSWORD`, `TRADING_MODE=paper`. Confirm it reaches login.
2. **Set `IBKR_*` on worker + api**: `IBKR_HOST=ib-gateway`, `IBKR_PORT=4002`, `IBKR_CLIENT_ID`,
   `IBKR_ACCOUNT_ID`, `IBKR_PAPER=true`; `ENGINE_BROKER=ibkr`; keep `DRY_RUN=true` first.
3. **Verify connectivity**: `GET /api/auth/status/ibkr` → `connected: true`; engine logs
   `[OPTIONS DRY RUN] …` during RTH.
4. **Paper smoke test**: `POST /api/options/order/ibkr` a 1-lot far-OTM contract with a cancel
   threshold; confirm in IB the order is `PEG STK` and that moving the underlying past the level
   cancels it.
5. **Go live on paper**: flip `DRY_RUN=false`; validate fills + qty cap + session buffers.
6. **Switch to live**: Gateway `TRADING_MODE=live`, `IBKR_PORT=4001`, `IBKR_PAPER=false`.

## Open decisions / risks to resolve before live
- **2FA (blocker for unattended live):** IBC full automation assumes no interactive second factor.
  Accounts on IBKR Secure Login System with IBKEY push need a **daily manual approval**. Resolve
  via a dedicated API username configured for headless login, or stay on paper. See
  `docs/ibkr-setup.md`.
- **Conditional cancel is intraday-only:** PEG STK orders are DAY orders, so the cancel-on-cross
  guard **expires at the close — it is NOT an overnight stop.** Decide whether an overnight
  protective exit (a separate native STP order) is required.
- **Param source:** confirm the runtime service will populate `peg_delta`,
  `cancel_if_underlying_above`, `cancel_if_underlying_below` per desired order, or rely on
  `IBKR_PEG_DELTA_DEFAULT` + no cancel.
- **`ib.portfolio()` assumption:** verify positions/option positions populate over the socket
  (may require an account-update subscription on connect); validate against paper.
- **`ib_async` threading** under concurrent engine + API calls — validate the dedicated-loop
  marshalling under load.
- **Pin `ib_async`:** currently `>=1.0.1` in requirements; built/tested against `2.1.0` — consider
  pinning a known-good version before live.

## Verification done so far
Unit tests only (no live e2e — no Gateway/creds in CI). Each unit's suite passes with the `IB`
object mocked (no socket): client 15 tests, engine/session-gating 16 tests, API 5 tests, plus the
existing suite. Live behavior is unverified pending the integration steps above.
