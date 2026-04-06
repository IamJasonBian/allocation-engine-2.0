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
2. `RH_PICKLE_B64` env var (base64-encoded)
3. Download from **Netlify Blobs** (durable external storage)
4. Seed stub with `RH_DEVICE_TOKEN` (triggers fresh login)

### Deploy ordering when session is stale
1. **First** run `python scripts/refresh_pickle.py` locally to upload a fresh pickle to Netlify Blobs
2. **Then** deploy — the service will pull the fresh pickle from Netlify on boot

If you deploy first, the service downloads the old/stale pickle from Netlify and fails.

## Robinhood Reauth Workflow

### `scripts/refresh_pickle.py`
- Interactive script — prompts for email, password, MFA
- Generates session pickle and uploads to Netlify Blobs
- Must be run locally (requires TTY for input)

### Device challenge mode
- If `rh.login()` times out (30s), Robinhood is requiring device approval via email
- Engine enters device challenge mode and sleeps until 11 AM ET (configurable)
- Check device challenge status: `GET /api/auth/status`

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
