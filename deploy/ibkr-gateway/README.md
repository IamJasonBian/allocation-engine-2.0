# IBKR gateway box — infrastructure

Infrastructure-as-code for the IBKR leg. The IB Gateway is a long-lived,
stateful, 2FA-authenticated JVM, so it runs on a **persistent GCE VM**, not
Cloud Run (stateless / scale-to-zero can't hold the session — see
`docs/IBKR_GATEWAY.md` constraint #4).

## Files

| File | What |
|---|---|
| `../../Dockerfile` | engine-ibkr image (only the IBKR broker enabled) |
| `docker-compose.yml` | ib-gateway (gnzsnz, IBC+Xvfb) + engine-ibkr, co-located |
| `provision.sh` | `gcloud compute instances create` for the VM |

## Layout

```
GCE VM (allocation-agent-service, e2-small, no external IP)
├── ib-gateway   ghcr.io/gnzsnz/ib-gateway:stable   (IBC + Xvfb, socket localhost)
└── engine-ibkr  build: this repo                    (IBKR leg, dials ib-gateway:4004)
```

Nothing is published; the API socket never leaves the compose network. Inspect
via SSH port-forward only. Credentials (`IB_USER` / `IB_PASSWORD`) come from GCP
Secret Manager on the box — never committed.

## Deploy

```bash
PROJECT=allocation-agent-service ./provision.sh
# then on the box: install docker, fetch secrets, and
TRADING_MODE=paper IBKR_PORT=4004 IBKR_PAPER=true DRY_RUN=true \
  docker compose -f deploy/ibkr-gateway/docker-compose.yml up -d
```

Ports: gnzsnz image exposes **4004 paper / 4003 live** (socat). Flip to live by
setting `TRADING_MODE=live IBKR_PORT=4003 IBKR_PAPER=false` — a deliberate act,
only after paper + `DRY_RUN=true` survives a weekly re-auth unattended.

## Local dev equivalent

The same IBC config runs locally against IB Gateway on this machine
(`~/ibc/config.ini`, port 4002 paper). `scripts/connect_probe.py` verifies the
socket in both places.
