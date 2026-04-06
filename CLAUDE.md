# Allocation Engine 2.0

## Architecture

- **allocation-engine-api** (`srv-d6sbe2k50q8c73fgn86g`) — Web service on Render, serves `/api/*` endpoints
- **allocation-engine-2.0** (`srv-d6i9evua2pns73901hj0`) — Background worker on Render, runs the engine loop
- Both deploy from `main` branch of `IamJasonBian/allocation-engine-2.0`
- Auto-deploy is **off** on the worker; deploys are triggered via API

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
