# Trailing stops: anchor the limit to the highest stop found, not to a % of today's price

A waterfall plan for changing **what the stop level is anchored to**. Today the
protective level is re-derived from the *current* price every time an order is
written (`price × (1 − STOP_TRAIL_PERCENT/100)`). This plan makes the level a
**high-water mark**: the highest stop ever established for that symbol becomes a
floor no replacement may sit below.

Complementary to `TRAILING_STOP_WATERFALL.md`, not a replacement — that plan
sets the *distance* (how wide the trail is per symbol); this one sets the
*level* (how low a written order is allowed to anchor). See §"Composing with
vol-scaling".

> **Percent note.** The repo default is `STOP_TRAIL_PERCENT = 16`
> (`app/stop_sweeper.py:50`); the worker env may set it to 12. Nothing below
> depends on which — read `TRAIL_PERCENT` as "whatever the configured trail
> distance is".

## The problem: the peg ratchets, the *order* does not

A Robinhood percentage `trailing_peg` does ratchet — but only for the lifetime
of one order. Every event that rewrites the order re-anchors it to the mark at
rewrite time, and there are three:

| Event | Where | What the new anchor is |
|---|---|---|
| GTC 90-day expiry → replace | `renew()` (`app/stop_sweeper.py:572`) | `build_payload()` is called with **no `current_price`**, so no `stop_price` — RH re-anchors the peg off the current mark |
| Row pruned, stop re-placed | `prune_missing()` (`:274`) → `sweep()` (`:486`) | `initial_stop_price(current_price, …)` = current mark × (1 − pct) |
| Live re-trail on σ change | Phase 5 of the vol-scaled plan | same `build_payload()` path |

So a symbol that peaked at \$100 carries a \$88 stop; it drifts to \$80 without
firing (a gap, a cancel, a partial); 90 days pass; `renew()` rewrites the order
and the protective level is now ~\$70. The 12/16% figure is a **distance**, and
the system stores no memory of the **level** it had already earned. The stop
walks down with the price, one rewrite at a time, and nothing logs that it did.

Nothing in the sqlite cache carries a level, either: the `stops` schema
(`:222`) holds `trail_percent` but no stop price, so even a manual audit can't
tell you where the level used to be.

## The mechanism

Persist one number per symbol — `hw_stop`, the highest protective level ever
established — and make every write respect it as a floor.

```
observed_stop_i  = level of the live RH order for symbol i      (see §σ of this plan)
hw_stop_i       ← max(hw_stop_i, observed_stop_i)               (monotone, per sweep)

on any write (place | renew | re-trail):
    target_stop = max(price × (1 − trail_pct/100), hw_stop_i)   ← floor applies
    peg_pct     = (1 − target_stop / price) × 100               ← back-solve
    clamp peg_pct to [TRAIL_FLOOR, TRAIL_CAP] ∩ (0, 50]
    payload.stop_price = target_stop; payload.trailing_peg.percentage = peg_pct
```

Two properties matter:

- **RH only accepts a percentage peg.** `validate_trailing_stop_payload()`
  (`:88`) requires `trailing_peg.type == "percentage"` with the value in
  `(0, 50]`. So "anchor to the high-water level" is *implemented* as a
  back-solved percentage plus an explicit `stop_price` — the guardrail and the
  auth-service payload builder are untouched. `stop_price` is already an
  allowed key (`_ALLOWED_PAYLOAD_KEYS`, `:72`).
- **The floor can only tighten.** `max(...)` never widens a stop, so the change
  cannot increase risk on any symbol. It can only refuse to give back a level
  already earned.

### Breach: when the floor is unreachable

If `price ≤ hw_stop`, the back-solved percentage is ≤ 0 and unplaceable. That is
not an error to round away — it means the market is already through a level the
book was supposed to protect (a gap-down, or a stop that was cancelled rather
than filled). **Never re-anchor lower to make the order placeable.** The write
is skipped, the symbol lands in `skipped` with `reason="hw_breach"`, and it is
surfaced as bot activity. Deciding whether the response is "sell now" or "alert
a human" is a policy call, out of scope here — but it must never be silent.

### Reset semantics (the sharp edge)

A monotone number needs explicit invalidation, or it becomes unplaceable
forever. `hw_stop` is dropped when, and only when:

- **the stop filled** — the position is out; the level protected what it was
  meant to;
- **the position closed and later re-entered** — a 2024 high-water against a
  2026 cost basis is nonsense. Keyed on a flat-to-nonzero quantity transition,
  which means the high-water row must **survive `prune_missing()`** (it deletes
  the `stops` row when a symbol leaves the book) — so this lives in its own
  table, not as a column on `stops`;
- **a corporate action** — a 4:1 split turns every high-water level into an
  apparent 75% breach overnight. Either adjust `hw_stop` by the split ratio or
  invalidate it and re-establish; doing neither wedges every affected symbol
  into permanent breach. **This is the highest-risk failure mode in the plan**
  and needs a named data source before Phase 3.

An operator escape hatch (`STOP_HW_RESET=SYM1,SYM2`, consumed once at sweep)
covers the cases the rules miss.

## Composing with vol-scaling

The two plans compose if ordered: `compute_trail_percents()` (`:414`) runs
first and yields the distance; the high-water floor is applied after, per
symbol, at write time. Consequence worth stating plainly, because the other doc
asserts an equality:

```
Σ wᵢ · trailᵢ  ≤  TRAIL_PERCENT        (was: = TRAIL_PERCENT)
```

The budget invariant becomes a **ceiling**. Any symbol whose floor binds ends up
tighter than its vol-scaled trail, which pulls the weighted average below
budget. That is the intended direction (never looser than today), but it means
the Phase-4 invariant check in `TRAILING_STOP_WATERFALL.md` must be re-stated as
an inequality once this ships, or the soak gate will read as a failure. Log both
numbers so the gap is attributable to binding floors rather than to a clamp bug.

## Where "the highest found stop" is read from

The one open decision. Two candidate sources, in preference order:

1. **The live RH order's own level.** `get_trailing_stop_orders()`
   (`auth-service/robinhood.py:296`) returns raw order dicts and `sweep()`
   already stores the whole thing in `stops.raw` (`:247`). If an active
   trailing-stop order carries its ratcheted trigger level, that is the true
   "highest found stop" with no estimation. **Requires a live read to confirm
   the field exists and updates** — the same discipline that found the missing
   `stop_price` on placement. Until confirmed, assume it does not.
2. **Derive from a peak-price watermark.** `hw_stop = peak_price × (1 − pct/100)`
   with `peak_price` a running max of observed marks. The sweeper's price today
   is position-implied (`market_value / qty`, `app/background.py:281`), which as
   the vol-scaling doc notes can be a stale RH mark — and a stale *high* mark is
   the dangerous direction, since it ratchets the floor up on a price that never
   traded. Needs a real quote source, or sampling discipline (daily closes only).

Source 1 is strictly better if it exists. Phase 1 is a live read to find out.

## Waterfall plan

### Phase 0 — Evidence

Confirm the problem is real rather than theoretical: from `stops.raw` history
and the RH order book, count symbols whose written stop level fell between two
consecutive orders, and by how much. If the answer is "never, materially", stop
here — the remaining phases aren't worth their risk.

*Exit:* a table of level regressions per symbol over the last ≥ 1 GTC cycle.

### Phase 1 — Requirements + source decision

- Live-read one active trailing-stop order; determine whether the ratcheted
  level is exposed (source 1) or must be derived (source 2).
- Name the corporate-action source. Fix the reset rules against it.
- Acceptance criteria: no written stop level below `hw_stop`; every breach
  logged and surfaced; no symbol wedged in breach after a split.

*Exit:* sign-off on the source, the reset rules, and the breach policy.

### Phase 2 — Design

- New table on the existing cache, deliberately *not* a column on `stops` so it
  survives pruning:
  ```sql
  CREATE TABLE IF NOT EXISTS stop_high_water (
    symbol       TEXT PRIMARY KEY,
    hw_stop      REAL NOT NULL,
    source       TEXT,            -- 'rh_order' | 'derived_peak'
    observed_at  TEXT,
    reset_reason TEXT,            -- last invalidation, for audit
    reset_at     TEXT
  );
  ```
  Schema migration on open, as the cache already does (`:222`).
- `apply_high_water(price, trail_pct, hw_stop) -> (stop_price, peg_pct) | None`
  as a pure function in `stop_sweeper.py` — deterministic, no I/O, `None` for
  breach. Same shape as `compute_trail_percents()`.
- Fix the latent gap while here: `renew()` must pass a price and a
  `stop_price`, or a renewal keeps re-anchoring at the current mark regardless
  of this feature.
- Rollout switch: `STOP_HIGH_WATER` env, default off — off is today's behavior,
  byte-for-byte.

*Exit:* function signature + schema diff reviewed.

### Phase 3 — Implementation

- The pure function, the high-water store, the write-path wiring behind the
  flag, the `renew()` price fix.
- `GET /api/viz/trailing-stops` gains `hw_stop`, `binding` (floor binds y/n),
  and `breach` per symbol, so the change is inspectable before it goes live.

*Exit:* a dry-run sweep produces a current-vs-proposed level report.

### Phase 4 — Verification

- Unit: floor binds / doesn't bind; breach returns `None` and never a negative
  peg; back-solved peg round-trips to the target level within the 0.5%
  quantum; reset on fill, on re-entry, on split; composition with a vol-scaled
  `trail_map`.
- Integration: dry-run soak across ≥ 5 sweeps **plus one full GTC renewal**,
  since renewal is where the regression actually lives — a soak that never
  renews cannot observe the bug being fixed.
- Gate: zero level regressions across the soak; every breach attributable.

*Exit:* soak report; go/no-go.

### Phase 5 — Deployment & maintenance

- Enable `STOP_HIGH_WATER` in the worker env. First live sweep tightens through
  the existing replace path, paced by `PLACE_DELAY_SECONDS`.
- Watch the first renewal cycle and the first corporate action.
- **Rollback:** unset the flag — next sweep writes percentage-anchored stops
  again. The high-water table is additive; leave it, it re-warms.

*Exit:* one renewal cycle live with no level regression and no wedged symbol.

## Open questions

- [ ] **Does the RH order expose its ratcheted level?** Phase 1 blocks on this.
      Everything downstream is cleaner if yes.
- [ ] **Corporate actions.** No source identified in this repo today. Without
      one, the first split wedges a symbol into permanent breach.
- [ ] **Breach policy.** Skip-and-alert is assumed above. "Market-sell on
      breach" is a materially different risk posture and is a separate decision.
- [ ] **Price quality.** Source 2 ratchets on `market_value / qty`; a stale high
      mark ratchets the floor to a price that never traded. Same gap the
      vol-scaling plan flagged, with the opposite failure direction.
- [ ] **Does the floor interact with the ≥1% replace deadband?** A binding floor
      that moves a stop by 0.4% shouldn't cost an RH round-trip; a breach should
      not be deadbanded away.
- [ ] **Options.** `sweep_options()` (`:620`) is a draft with no order model;
      high-water is meaningless there until it has one. Explicitly out of scope.
