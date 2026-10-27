# Behavior map

Observed behaviors of the broker legs and the auth-service client, each with
evidence. This is a living record: add rows when you find something, and
update `status` / `verified` when a probe or fix changes the answer.

| File | Covers |
|---|---|
| `robinhood.jsonl` | `RobinhoodTrader`, `/api/trade/*` on the Robinhood leg |
| `ibkr.jsonl` | `IBKRTrader` and the gateway box (integration pending) |
| `allocation-client.jsonl` | `AuthServiceClient`, `box_session`, and the auth-service routes it calls |

## Row shape

One JSON object per line:

- `id`: stable key, prefixed `rh-`, `ibkr-`, or `ac-`
- `behavior`: what happens, from the caller's point of view
- `entry`: how a caller reaches it
- `evidence`: `path:line` anchors at `ref`, or `commit <sha>`
- `proof`: how to observe it
- `status`: `observed` | `gap` (behavior differs from what the code or docs promise) | `pending` (not live yet) | `drift` (docs disagree with code) | `fixed`
- `tested`: whether an existing test covers it
- `verified` / `verified_by`: date and evidence file or test that last confirmed it
- `ref` / `seen`: commit the anchors point at, and when the row was written
- optional: `severity`, `notes`

## Re-running the probes

The probes only touch stubs and a local auth-service bound to 127.0.0.1. They
make no broker or box calls. Pass the repo root:

```bash
python3 docs/behaviors/probes/probe_box.py "$PWD" "$(mktemp -d)" > docs/behaviors/evidence/probe_box.jsonl
python3 docs/behaviors/probes/probe_clients.py "$PWD" > docs/behaviors/evidence/probe_clients.jsonl
DRY_RUN=true python3 docs/behaviors/probes/probe_trade.py "$PWD" > docs/behaviors/evidence/probe_trade.jsonl
```

Each output line has a `probe` key that matches a row `id`. A diff in
`evidence/` after a code change means a behavior moved.
