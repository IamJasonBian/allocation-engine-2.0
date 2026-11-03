# Allocation Engine 2.0

## Services (Render, deploy from `main`, auto-deploy off: `render deploys create <id> --commit <sha>`)

| Service | ID | Type |
|---|---|---|
| allocation-engine-api — `/api/*` | srv-d6sbe2k50q8c73fgn86g | web |
| allocation-engine-2.0 — engine loop | srv-d6i9evua2pns73901hj0 | worker |
| allocation-feed | srv-d6kcm3ua2pns738r97a0 | worker |

Logs: `render logs -r <id> --limit 50 -o text`. Token expired → `render login`.
Deploys are safe: the engine holds no RH credentials and re-fetches a token from the box on boot (`app/box_session.py`).
Health: `GET /api/health`, `GET /api/auth/status[/robinhood]`.

## Boundaries
- Work on core logic (`app/`, `main.py`) **or** `auth-service/`, never both in one change.
- Core logic only **reads** the box (`/auth/status`, `/token`, GET order routes); box VM deploys/config are their own task.
- Robinhood login runs **only** on the box. Nothing here may call `rh.login()` or reintroduce pickle/TOTP/device-token flows. On RH `401`: re-vend from `GET /token` once, retry.

## auth-service box
- VM `allocation-engine-auth-service-prod`, `us-central1-c`, project `route-manager-prod`, owner cloud@optimchain.org.
- `https://34-30-182-125.sslip.io` (Caddy → `:8080`). Firewall allows Render egress only; laptops/Netlify time out — use SSH.
- Install `~/auth-service` (venv `./venv`, config `env.prod`; secrets are Secret Manager names).
- `gcloud compute ssh allocation-engine-auth-service-prod --zone us-central1-c --project route-manager-prod`

## Dashboard data
Only the engine can reach the box, so it writes the dashboard's data to the Trading DB (Netlify functions):
- orders → `trading_db.post_orders` → `db-orders`
- positions/options/account → `trading_db.post_positions` → `db-positions`, `order-book-snapshot` (whole-book replace: missing symbols are deleted)
- bot activity → `trading_db.post_bot_activity` → `db-bot-activity`

Both syncs run every `TRADING_DB_SYNC_SECONDS` (900s) and are **not** gated on `DRY_RUN` — gating them is what froze positions before.
