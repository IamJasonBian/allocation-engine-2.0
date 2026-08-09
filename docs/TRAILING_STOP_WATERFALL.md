# Trailing stops: volatility-scaled percentages under a fixed risk budget

A waterfall plan for improving **only the trailing-stop percentages** — which
symbol gets what trail — while keeping the overall risk profile exactly where
it is today. Each phase completes and signs off before the next begins.

## The problem with the flat 16%

`stop_sweeper.py` places a percentage trailing stop of `STOP_TRAIL_PERCENT`
(16%) on every ticker in the universe. A flat percentage means opposite
failure modes at the two ends of the volatility spectrum:

- **High-vol names** (σ ≈ 45% annualized): 16% is ~2 weeks of ordinary noise —
  stops fire on wiggle, positions exit that the engine still wants.
- **Low-vol names** (σ ≈ 12%): 16% is a catastrophic-only stop — it gives back
  months of gains before triggering, protecting far less than it could.

## The improvement, and the invariant that freezes the risk profile

Scale each symbol's trail to its measured volatility, under a budget
constraint that pins the portfolio-level giveback to today's number:

```
trail_i = TRAIL_PERCENT × (σ_i / σ̄_w)          σ̄_w = Σ w_i σ_i
subject to  Σ w_i · trail_i = TRAIL_PERCENT      (the invariant)
            floor ≤ trail_i ≤ cap                (clamp, then renormalize)
```

where `w_i` is the symbol's market-value weight within the stopped universe
and `σ_i` is 20-day realized volatility of daily closes. The weighted-average
trail — the maximum aggregate giveback the stop book tolerates — stays exactly
`TRAIL_PERCENT`. Only the *distribution* across names changes: wide where
noise is wide, tight where it's tight. Nothing else moves — same universe,
same sweep cadence, same GTC/renewal path, same `(0, 50]` guardrail, and
`validate_trailing_stop_payload()` is untouched.

Secondary rules (all %-only, all inside the invariant):

- **Clamp** to `[8%, 24%]`, then renormalize the unclamped names so the
  invariant survives clamping (water-filling, a few iterations).
- **Quantize** to 0.5% steps and only replace a live stop when the new trail
  differs by ≥ 1% — the replace path costs an RH round-trip and stops churn
  is its own risk.
- **Degenerate data** (missing bars, σ ≈ 0): fall back to the flat 16% for
  that symbol and log it; never guess.

## Waterfall plan

### Phase 0 — Observability baseline ✅ (this PR)

The measurement surface the later phases verify against:
`/api/viz/drift` (per-symbol drift vs the rail), `/api/viz/order-funnel`
(placed→filled + slippage), `/api/viz/wheel-lanes` (per-underlying state),
each on top of the IBKR broker client.

*Exit criteria:* endpoints merged, full test suite green. **Met.**

### Phase 1 — Requirements

- Freeze the invariant definition (weights: market-value share of the stopped
  universe at sweep time) and the clamp bounds.
- Pick the σ estimator (20-day close-to-close vs 14-day ATR%) and its data
  source: IBKR daily bars via the gateway, or the runtime market-data service.
- Acceptance criteria: invariant holds to ±0.1% after clamping; no symbol
  outside `[floor, cap]`; degenerate-data fallback observable in logs.

*Artifacts:* one-page requirements note appended to this doc.
*Exit:* sign-off on formula, bounds, and estimator.

### Phase 2 — Design

- `compute_trail_percents(positions, sigmas, budget) -> dict[symbol, pct]` as
  a pure function in `stop_sweeper.py` — deterministic, no I/O, testable alone.
- SQLite additions on the existing stops cache: `trail_pct`, `sigma`,
  `computed_at` per symbol (schema migration on open, as the cache already does).
- Rollout switch: `STOP_VOL_SCALED` env, default off — off means today's flat
  behavior, byte-for-byte.

*Artifacts:* function signature + schema diff reviewed.
*Exit:* design review sign-off; no guardrail or auth-service changes required.

### Phase 3 — Implementation

- The pure function, the σ fetch, the sweep wiring behind the flag.
- `GET /api/viz/trailing-stops` — current vs proposed trail per symbol with
  σ and the invariant check, so the change is inspectable before it's live
  (joins the Phase-0 suite).

*Exit:* code complete, dry-run sweep produces a proposed-vs-current report.

### Phase 4 — Verification

- Unit: invariant under clamping, quantization, single-symbol universe,
  σ = 0 fallback, deadband suppression.
- Integration: dry-run soak across ≥ 5 daily sweeps; compare proposed stop-out
  frequency against the flat book using the Phase-0 endpoints.
- Gate: invariant within tolerance on every soak day.

*Exit:* soak report reviewed; go/no-go for live.

### Phase 5 — Deployment & maintenance

- Enable `STOP_VOL_SCALED` in the worker env; first live sweep replaces stops
  through the existing renew path, paced by `PLACE_DELAY_SECONDS`.
- Monitor a full GTC renewal cycle via `/api/viz/trailing-stops`.
- **Rollback:** unset the flag — next sweep restores flat 16% through the same
  replace path. No schema rollback needed.

*Exit:* one renewal cycle live with the invariant holding; plan closed.

## Gaps — ticker service vs this plan (checked 2026-08-09)

Findings from checking the market-data-service (Scala → Redis `market-quotes`)
against the trailing-stop implementation. These block or shape Phase 1's
σ-source decision:

- [ ] **No σ source exists today.** The ticker service publishes bid/ask/mid
  quotes only — no OHLC, no daily closes. Its `market-quotes:history` list
  holds ~8 hours (3s polls, LTRIM 10000), so a 20-day close-to-close σ cannot
  be computed from Redis. Either commit to IBKR daily bars via the gateway, or
  add a daily-close rollup to the Scala service (it already speaks Alpaca —
  a bars poller writing ~20 closes/symbol to a `market-closes` key is small).
- [ ] **Downstream mirrors are empty/stale.** The runtime service's
  `/api/market-data` serves a stale snapshot (`ticker_metrics: {}`, May 2026)
  and the Netlify Blobs `market-quotes` archive store lists zero blobs. Fix or
  retire before naming either as the σ source.
- [ ] **Sweeper prices bypass the ticker service.** `sweep()` sets the initial
  `stop_price` from position-implied price (`market_value / qty`), which can
  be a stale RH mark; live quotes in Redis go unused. Decide whether stops
  should price off the ticker service mid when fresh.
- [ ] **Universe mismatch is silent.** Sweep universe is
  `STOP_TICKERS ∪ book`; the quote poller has its own symbol list. Symbols
  the σ source doesn't cover fall back to flat 16% — the fallback log line
  must make these visible during the Phase-4 soak.
- [ ] **Estimator discipline.** If σ is ever computed from intraday mids
  instead of daily closes, spread jitter inflates σ for thin names — stay on
  closes (the payload's `spread_bps` can gate outliers if needed).
- [ ] **Replace-path deadband not yet wired.** `compute_trail_percents()` and
  the `trail_map` sweep parameter are in (see below), but the ≥1% replace
  deadband only matters once live stops are re-trailed on σ change — wire it
  with the σ fetch in Phase 3.

### Status note

`compute_trail_percents(market_values, sigmas)` landed as the Phase-2 pure
function in `stop_sweeper.py`: budget invariant with worst-violator-first
clamp/renormalize, 0.5% quantize, flat-budget fallback per degenerate symbol.
`sweep()` accepts a per-symbol `trail_map`, and the background loop builds one
behind `STOP_VOL_SCALED` (default off = flat 16%, byte-for-byte). With no σ
source wired, the flag-on path also degrades to flat 16% per symbol, logged —
so the implementation is 16%-compatible in every configuration until a σ
source from the gaps above is chosen.
