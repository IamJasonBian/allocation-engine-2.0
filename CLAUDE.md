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
identity.

### auth-service box — where it lives

| | |
|---|---|
| Owner | **cloud@optimchain.org** — owns this GCP account |
| Instance | `allocation-engine-auth-service-prod` |
| Zone / project | `us-central1-c` / `route-manager-prod` |
| Public URL | `https://34-30-182-125.sslip.io` (Caddy → `localhost:8080`) |
| Install dir | `~/auth-service` (venv at `./venv`, config `env.prod`) |
| Ingress | GCP firewall allows **Render egress IPs only** — Netlify and laptops time out (`UND_ERR_CONNECT_TIMEOUT`); use a `gcloud compute ssh` port-forward |

```bash
gcloud compute ssh allocation-engine-auth-service-prod \
  --zone us-central1-c --project route-manager-prod
```

Credentials live in GCP Secret Manager; `env.prod` holds only the project id
and secret *names*. Reaching Secret Manager needs the instance service account,
so anything touching the box runs as the owner above.

## Render Deploy

Deploys are safe for session state: this service holds no Robinhood
credentials and never logs in. On boot it fetches an access token from the
auth-service box (`GET /token`, cached in sqlite by `app/box_session.py`), so
an ephemeral filesystem costs at most one token re-fetch.

The pickle/device-token/TOTP flow that used to live here — `pickle_store.py`,
`scripts/refresh_pickle.py`, `refresh_pickle_watch.py`, `RH_PICKLE_*`,
`RH_DEVICE_TOKEN`, `RH_TOTP_SECRET` — was removed once the box took over
authentication. Nothing in this repo may reintroduce a direct RH login.

After deploy, check logs:
`render logs -r <service-id> --limit 30 -o text --direction backward`

## Where the dashboard's data comes from

The engine is the **only** component that can reach the auth-service box: the
box's GCP firewall allows Render egress IPs and nothing else. Netlify functions
time out against it (`UND_ERR_CONNECT_TIMEOUT`) — that is why
`snapshot-refresh.cjs` in `allocation-manager` cannot serve as a replacement
producer without a firewall change, which is an auth-service task.

So the engine reads Robinhood and writes everything the dashboard needs into
the **Trading DB** (Postgres, behind the 5thstreetcapital Netlify functions):

| What | Written by | Read by |
|---|---|---|
| stock + option orders | `trading_db.post_orders` | `db-orders` |
| positions, options, account | `trading_db.post_positions` | `db-positions`, `order-book-snapshot` |
| bot activity | `trading_db.post_bot_activity` | `db-bot-activity` |

Both writes run on the same ungated cadence (`TRADING_DB_SYNC_SECONDS`,
default 900s). **They are deliberately not gated on `DRY_RUN`** — a dry-run
engine still reads the real book, and the dashboard should reflect it.
Gating the old Netlify Blobs "engine snapshot" on `is_live` is exactly what
froze positions while orders stayed current.

`post_positions` is a whole-book replace: symbols absent from the payload are
deleted downstream, so a closed position disappears rather than lingering.

### Auth status endpoint
- `GET /api/auth/status` — returns `authenticated`, `device_challenge_pending`, `email`
- `GET /api/auth/status/robinhood` — broker-specific status
- `GET /api/health` — general service health

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
