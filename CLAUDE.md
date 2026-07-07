# Allocation Engine 2.0

## Architecture

- **allocation-engine-api** (`srv-d6sbe2k50q8c73fgn86g`) — Web service on Render, serves `/api/*` endpoints
- **allocation-engine-2.0** (`srv-d6i9evua2pns73901hj0`) — Background worker on Render, runs the engine loop
- Both deploy from `main` branch of `IamJasonBian/allocation-engine-2.0`
- Auto-deploy is **off** on the worker; deploys are triggered via API

## Service boundaries

**When working on the core-logic (allocation-engine loop, `app/`, `main.py`),
touch only core-logic code. When working on the auth-service, touch only
`auth-service/`.** The two services must not touch each other outside of
**read** interactions:

- Core-logic may **call** the auth-service's read endpoints (`/auth/status`,
  `/token`, `GET /orders/trailing_stop`) — it must not modify `auth-service/`
  code, its VM, or its config.
- The auth-service never calls into core-logic.
- Changes to the running auth-service VM (deploy/restart/config) are their own
  task — never a side effect of core-logic work.

**Robinhood authentication runs ONLY in the auth-service box.** No other
component ever runs `robinhood.authenticate` / `rh.login()` / password+TOTP
flows. Consumers get a live bearer from the box's `GET /token` and treat an RH
`401` as "re-vend once, retry" — the box owns login, refresh, and device
identity. (Legacy exception, to be migrated: the Render services' pickle flow
documented below still logs in directly; no new code may.)

## Render Deploy

Deploys are **destructive to session state** — Render's ephemeral filesystem wipes the local Robinhood pickle on every deploy.

### Pickle restore priority (on boot)
1. Local pickle on disk (gone after deploy)
2. `RH_PICKLE_B64` env var (base64-encoded) — **removed as of 2026-04-06**, was causing stale session issues
3. Download from **Netlify Blobs** (durable external storage)
4. Seed stub with `RH_DEVICE_TOKEN` (triggers fresh login)

### Deploy checklist
1. **First** run `python scripts/refresh_pickle.py` locally to upload a fresh pickle to Netlify Blobs
2. **Then** deploy — the service will pull the fresh pickle from Netlify on boot
3. After deploy, check logs: `render logs -r <service-id> --limit 30 -o text --direction backward`

If you deploy first, the service downloads the old/stale pickle from Netlify and fails.
**Always regenerate the pickle before a fresh deploy.**

### Token lifetime and self-healing
- Robinhood access tokens last ~24 hours
- Within that window, code-only deploys are safe — Render pulls the still-valid pickle from Netlify
- After the token expires, `_ensure_auth()` detects it and `_login()` fires with credentials + TOTP from env vars
- `_seed_device_token()` ensures the correct device token is in the pickle before login, so `_login()` should self-heal without a device challenge as long as the device is trusted
- If Robinhood revokes device trust (rare), `refresh_pickle.py` must be re-run locally

## Robinhood Reauth Workflow

### `scripts/refresh_pickle.py`
- Generates session pickle with the static device token and uploads to Netlify Blobs
- Uses a temp directory — does not modify local session state
- Reads `RH_USER`, `RH_PASS`, `RH_TOTP_SECRET` from env vars (falls back to interactive prompts)
- Must be run locally (requires TTY if env vars are not set)

### Device token
- The static device token (`8508c7fc-...`) in `Config.RH_DEVICE_TOKEN` is the approved device identity
- `_login()` calls `_seed_device_token()` to ensure the pickle always has this token before calling `rh.login()`
- If the pickle has a **different** device token, Robinhood triggers a device challenge
- `refresh_pickle.py` also seeds this token so the uploaded pickle matches Render's identity

### Device challenge mode
- If `rh.login()` times out (30s), Robinhood is requiring device approval via email
- Engine enters device challenge mode and sleeps until 11 AM ET (configurable)
- Check device challenge status: `GET /api/auth/status`

### 429 rate limit storm (robin_stocks bug)
- When a device challenge is triggered, `robin_stocks` polls `get_prompts_status` in a `while True` loop with no timeout
- Our 30s login timeout kills the thread, but the damage is done — Robinhood 429s cascade
- **Prevention:** ensure the pickle always has the correct static device token before login (handled by `_seed_device_token()`). If the device is trusted, the verification workflow is never triggered
- This cannot be fixed without patching robin_stocks itself

### Auth status endpoint
- `GET /api/auth/status` — returns `authenticated`, `device_challenge_pending`, `email`
- `GET /api/auth/status/robinhood` — broker-specific status
- `GET /api/health` — general service health

## Interactive Brokers (IBKR)

An IBKR broker places **live options orders, always as Pegged-to-Stock (`PEG STK`)**, with an
optional conditional cancel (cancel the working order if the underlying trades above/below a
threshold). It connects via [`ib_async`](https://github.com/ib-api-reloaded/ib_async) over the
TWS socket to a persistent **IB Gateway** running as a separate private Render service
(`deploy/ib-gateway/`, dockerized `gnzsnz/ib-gateway`; ports 4001 live / 4002 paper). `PEG STK`
and native cancel-on-price conditions are TWS/Gateway-only — the Client Portal Web API can't do
them, which is why a Gateway process is required. **Caveat:** pegged orders are DAY orders and
their cancel guards are **intraday-only** (not an overnight stop); the engine re-stages each RTH
session and gates submission with open/close buffers (`IBKR_OPEN_BUFFER_MIN`/`IBKR_CLOSE_BUFFER_MIN`).
Secrets (`TWS_*` on the gateway; `IBKR_*`, `ENGINE_BROKER=ibkr`, `DRY_RUN` on worker+api), the
2FA operational risk, and paper-first rollout are documented in
[docs/ibkr-setup.md](docs/ibkr-setup.md).

## Render CLI

```bash
render logs -r <service-id> --limit 50 -o text     # view logs
render services list -o json                         # list services
```

API key stored in `~/.render/cli.yaml`. Tokens expire — run `render login` to refresh.

## Key Service IDs

| Service | ID | Type |
|---------|-----|------|
| allocation-engine-api | srv-d6sbe2k50q8c73fgn86g | web |
| allocation-engine-2.0 | srv-d6i9evua2pns73901hj0 | worker |
| allocation-feed | srv-d6kcm3ua2pns738r97a0 | worker |

# CLAUDE.md

Behavioral guidelines to reduce common LLM coding mistakes. Merge with project-specific instructions as needed.

**Tradeoff:** These guidelines bias toward caution over speed. For trivial tasks, use judgment.

## 1. Think Before Coding

**Don't assume. Don't hide confusion. Surface tradeoffs.**

Before implementing:
- State your assumptions explicitly. If uncertain, ask.
- If multiple interpretations exist, present them - don't pick silently.
- If a simpler approach exists, say so. Push back when warranted.
- If something is unclear, stop. Name what's confusing. Ask.

## 2. Simplicity First

**Minimum code that solves the problem. Nothing speculative.**

- No features beyond what was asked.
- No abstractions for single-use code.
- No "flexibility" or "configurability" that wasn't requested.
- No error handling for impossible scenarios.
- If you write 200 lines and it could be 50, rewrite it.

Ask yourself: "Would a senior engineer say this is overcomplicated?" If yes, simplify.

## 3. Surgical Changes

**Touch only what you must. Clean up only your own mess.**

When editing existing code:
- Don't "improve" adjacent code, comments, or formatting.
- Don't refactor things that aren't broken.
- Match existing style, even if you'd do it differently.
- If you notice unrelated dead code, mention it - don't delete it.

When your changes create orphans:
- Remove imports/variables/functions that YOUR changes made unused.
- Don't remove pre-existing dead code unless asked.

The test: Every changed line should trace directly to the user's request.

## 4. Goal-Driven Execution

**Define success criteria. Loop until verified.**

Transform tasks into verifiable goals:
- "Add validation" → "Write tests for invalid inputs, then make them pass"
- "Fix the bug" → "Write a test that reproduces it, then make it pass"
- "Refactor X" → "Ensure tests pass before and after"

For multi-step tasks, state a brief plan:
```
1. [Step] → verify: [check]
2. [Step] → verify: [check]
3. [Step] → verify: [check]
```

Strong success criteria let you loop independently. Weak criteria ("make it work") require constant clarification.

---

**These guidelines are working if:** fewer unnecessary changes in diffs, fewer rewrites due to overcomplication, and clarifying questions come before implementation rather than after mistakes.
