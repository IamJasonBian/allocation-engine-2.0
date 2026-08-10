# Order lookup / patch / cancel

Order-management surface for momentum-style execution (walk a resting limit
toward the market) and clean teardown. Core-logic side is implemented; two box
routes remain (an auth-service task).

## Endpoints (core-logic, behind a request token)

All three are gated by `DASHBOARD_REQUEST_TOKEN` (Bearer, fail closed — unset
means 503), the same scheme as the render-logs function.

| Method | Path | Box route | Status |
|---|---|---|---|
| GET | `/api/robinhood/orders/<id>` | `GET /orders/{id}` | client+API done; **box route needed** |
| POST | `/api/robinhood/orders/<id>/replace` | `POST /orders/trailing_stop/replace` (exists) | **usable once deployed** |
| POST | `/api/robinhood/orders/<id>/cancel` | `POST /orders/{id}/cancel` | client+API done; **box route needed** |

Robinhood has no HTTP PATCH for orders: a "patch" is `POST /orders/{id}/replace/`
with a full order body and a `ref_id`; RH cancels the old order and returns a
new one whose `replaces` points back at it.

## Design principles (grounded in DDIA references)

- **Fencing tokens / leases** (Gray & Cheriton 1989; DDIA ch. 8). Each replace
  supersedes the previous order id — a writer holding a stale id must be fenced
  out. RH's `replaces` chain plus `is_editable` is the fence: `walk_order()`
  always writes against the current head and stops when an order is no longer
  editable. Never re-issue against a superseded id.
- **End-to-end idempotency** (Saltzer/Reed/Clark 1984; Jouravlev 2004; DDIA
  ch. 12). Dedup must happen at the endpoint, not the transport. `ref_id` is
  RH's idempotency key: a *new intent* gets a fresh id, a *retry of the same
  intent* reuses it (`build_replace_payload(..., ref_id=...)`) so RH collapses
  duplicates. This is the fix for the order-stacking failure mode.
- **Idempotent cancel.** An already-gone order (cancelled or filled) is a
  success, not an error — `cancel_order` treats 404/"already" as
  `{cancelled, already_gone: True}` so retries converge.
- **Read-after-write confirmation.** The POST response is a hint; the endpoint
  is the source of truth. Confirm a walk/cancel via `get_order` rather than
  trusting the mutation response alone.
- **Saga compensation** (Garcia-Molina & Salem 1987). A walk is a sequence of
  replaces; on a failed step, stop and report the last-good id rather than
  leaving an ambiguous partial.

## Remaining work (auth-service task — not core-logic)

1. Add `GET /orders/{id}` and `POST /orders/{id}/cancel` box routes (the RH
   calls are `orders_url(id)` and `cancel_url(id)` respectively; `robin_stocks`
   has `get_stock_order_info` / `cancel_stock_order`, replace is raw).
2. The box's replace guardrail (`check_trailing_stop_payload`) only permits
   trailing-stop shapes, and its deployed enforcement is lax (see the security
   finding: a plain limit order placed live). Decide the guardrail policy for
   generic order shapes before enabling walk in production.
3. The existing `POST /api/robinhood/trailing-stop` place route is **not** token
   gated — it should get the same `_require_token()` gate (separate follow-up).
