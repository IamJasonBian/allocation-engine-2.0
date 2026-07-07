# IB Gateway deployment

Headless [Interactive Brokers Gateway](https://www.interactivebrokers.com/en/trading/ibgateway-stable.php)
packaged by [`gnzsnz/ib-gateway-docker`](https://github.com/gnzsnz/ib-gateway-docker).
The image bundles **IBC** (automates the Gateway login), **Xvfb** (a virtual X
display so the Java GUI runs without a screen), and **socat** (forwards the
in-container API port to the host). The engine talks to it over the TWS socket
API using [`ib_async`](https://github.com/ib-api-reloaded/ib_async).

This is the persistent process that `ENGINE_BROKER=ibkr` requires: native
**Pegged-to-Stock (`PEG STK`)** orders and `PriceCondition` conditional cancels
are only available over the TWS socket API, not the Client Portal Web API.

Ports: **4002 = paper**, **4001 = live**.

## Local (paper) testing

```bash
cp .env.example .env          # fill TWS_USERID / TWS_PASSWORD (paper account)
docker compose up             # boots IBC -> Gateway, exposes 4002
```

Point the engine at it:

```bash
IBKR_HOST=127.0.0.1 IBKR_PORT=4002 IBKR_PAPER=true ENGINE_BROKER=ibkr DRY_RUN=true
```

Validate parsing without booting:

```bash
docker compose config
```

To watch the Gateway GUI while debugging, uncomment the `5900:5900` VNC port in
`docker-compose.yml` and connect a VNC client to `localhost:5900`.

## Render deployment

Deploy as a **separate private service** (`type: pserv`) — see `render.yaml`. A
private service has no public URL and is reachable only from other services in
the same Render account/region, which is exactly what a credential-holding
broker gateway should be.

1. Create the service from `deploy/ib-gateway/render.yaml` (or add it to the
   root blueprint). Set `TWS_USERID` / `TWS_PASSWORD` as secrets in the Render
   dashboard (`sync: false`).
2. Keep its **region** equal to the worker/api region so private networking
   works.
3. On the **worker** and **api**, set `IBKR_HOST=ib-gateway` (the gateway
   service `name`) and `IBKR_PORT=4002` (paper) / `4001` (live).
4. Start with `TRADING_MODE=paper`. Flip to `live` only after the paper
   validation in `docs/ibkr-setup.md` passes.

> **2FA warning:** unattended login assumes IBKR's Secure Login System / 2FA is
> disabled for this username, or that a dedicated API user is used. A username
> enforcing IBKEY push needs a **daily manual approval**, which breaks
> unattended live trading. See `docs/ibkr-setup.md` § "Operational risk — 2FA".

The Gateway re-logs in daily (`AUTO_RESTART_TIME`, default `23:45` ET) — a quiet
overnight window, clear of the 09:30 ET open.

See **`docs/ibkr-setup.md`** for the full secrets table, session-handling
caveats, and the go-live runbook.
