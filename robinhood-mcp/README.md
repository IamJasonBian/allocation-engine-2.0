# robinhood-mcp — local Robinhood MCP box

A simple, standalone MCP server (stdlib-only, stdio transport) that exposes our
Robinhood logic as MCP tools. It exists because the official Robinhood MCP
(`agent.robinhood.com/mcp/trading`) requires an agentic-OAuth token that can't
be minted headlessly — this box provides the same kind of tool surface backed
by our own session token instead, and can be added to the full flow later
(relay from the auth-service, or `claude mcp add` directly).

## Trade safety (structural)

- **No buy/sell/cancel tools exist.** The tool catalog is reads plus exactly
  one write: percentage trailing stops.
- The trailing-stop tools take scalar arguments (`symbol`, `side`, `quantity`,
  `trail_percent`) and build the order payload internally — a caller can never
  supply raw order JSON.
- `dry_run` defaults to `true` on both write tools.

## Tools

| Tool | What |
|------|------|
| `get_stock_orders` | Stock orders (paginated, symbols resolved) |
| `get_option_orders` | Option orders |
| `get_positions` | Current stock positions |
| `get_trailing_stop_orders` | Active percentage trailing stops |
| `place_trailing_stop` | The sanctioned write; `dry_run` default true |
| `replace_trailing_stop` | Replace by order id; `dry_run` default true |
| `sync_trading_db` | Push RH order history → 5thstreetcapital Trading DB |

## Auth — all of it goes through the auth-service box

Every RH token is vended from the auth-service `GET /token`; there is no
local-token path. Set both:

- `AUTH_SERVICE_URL` — the box (`https://34-30-182-125.sslip.io` from Render,
  whose IPs are allowlisted; from a laptop, port-forward:
  `gcloud compute ssh allocation-engine-auth-service-prod --zone us-central1-c
  --project route-manager-prod -- -N -L 8080:localhost:8080` then use
  `http://127.0.0.1:8080`).
- `AUTH_SERVICE_TOKEN` — the box's exec bearer.

On an RH `401` the server re-vends once and retries — the box owns
refresh/login entirely. This is a **read-only** consumer of the auth-service
(`/token`), per the service-boundary rule in the repo README.

## Run

```bash
# as an MCP server for Claude Code
claude mcp add robinhood-local -- python3 /path/to/robinhood-mcp/server.py

# tests (no network)
cd robinhood-mcp && python3 -m unittest discover -s tests
```

The Trading DB target defaults to
`https://5thstreetcapital.netlify.app/.netlify/functions` (override with
`TRADING_DB_BASE`). Its write endpoints are deliberately open — see
`/docs` on that site for the contract.
