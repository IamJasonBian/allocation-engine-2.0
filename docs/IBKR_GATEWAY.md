# IBKR Gateway — how and where it runs

`app/brokers/ibkr_client.py` routes orders over a socket to a running TWS /
IB Gateway process. IBKR has no REST order endpoint this stack can use, so
live IBKR routing means keeping an authenticated IBKR desktop process alive
somewhere, and deciding where "somewhere" is. This repo already solved the
same shape of problem once — the Robinhood auth-service box — and the answer
here mirrors it, with one critical difference.

## Constraints

1. **It's a session, not an API key.** Interactive login (username, password,
   IB Key 2FA on the phone). Daily restarts; ~weekly full re-auth; Sunday
   maintenance windows. Same class of problem the auth-service box owns for
   Robinhood.
2. **One session per username.** A laptop login kicks the gateway's session.
   Paper and live are separate usernames, which softens this.
3. **The socket is unauthenticated and un-TLS'd.** Anything that can reach
   `IBKR_HOST:IBKR_PORT` can trade the account. No bearer token exists.
4. **It's a long-lived JVM wanting a display** (Xvfb in containers).
   Serverless and scale-to-zero are out; this needs a machine.

**Ports:** IB Gateway 4002 paper / 4001 live (TWS: 7497 / 7496). The client
defaults to `127.0.0.1:4002`.

## Why the auth-service pattern does NOT transfer directly

The auth box is reachable because two layers stack: the GCP firewall admits
Render egress IPs, **and** every request needs
`RH_AUTH_SERVICE_REQUEST_TOKEN` over HTTPS. Render egress IPs are shared by
every Render customer, so the firewall alone is not an auth boundary — the
bearer token is what actually gates the box.

The IBKR socket has no token layer (constraint 3). A firewall rule admitting
Render egress to port 4002 would let any Render tenant place trades. So the
one thing we must not build is "gateway box, firewall open to Render" —
the pattern that is safe for the auth box is unsafe here.

## Options

| Option | Verdict |
|---|---|
| **A. Gateway box + IBKR engine leg co-located** — socket never leaves localhost | **target** |
| B. SSH tunnel from the Render worker to the box | workable, but key management + tunnel liveness inside a Render container is fragile ops for no architectural gain |
| C. stunnel/mTLS wrapper in front of the socket, firewalled to Render | real auth, but bespoke; more moving parts than A for the same result |
| D. Move the whole engine onto the box | over-reach; Render deploy flow and the Robinhood/Alpaca legs work today |

## Phase 1 — now (dev / paper): TWS on the laptop

```bash
# TWS: Global Configuration → API → Settings:
#   Enable ActiveX and Socket Clients · untick Read-Only API
#   port 7497 · Trusted IPs 127.0.0.1
ENABLED_BROKERS=robinhood,alpaca,ibkr IBKR_PORT=7497 \
  python main.py --broker ibkr status
```

Right for development and keyboard-attended paper orders. Dies with the lid;
cannot back the worker loop.

## Phase 2 — continuous: gateway box, IBKR leg co-located

A GCP VM in the same account as the auth-service box (owner
**cloud@optimchain.org**), e2-small (2 GB is the JVM floor), running
[gnzsnz/ib-gateway](https://github.com/gnzsnz/ib-gateway-docker)
(IB Gateway + IBC + Xvfb) and a second container from **this repo** with only
the IBKR broker enabled:

```yaml
# docker-compose.yml on the ibkr-gateway box
services:
  ib-gateway:
    image: ghcr.io/gnzsnz/ib-gateway:stable
    restart: always
    environment:
      TWS_USERID: ${IB_PAPER_USER}        # from GCP Secret Manager, like env.prod
      TWS_PASSWORD: ${IB_PAPER_PASSWORD}
      TRADING_MODE: paper                  # flipping to live is a deliberate act
      AUTO_RESTART_TIME: "11:59 PM"
    # no `ports:` — nothing published, ever

  engine-ibkr:
    build: .
    restart: always
    environment:
      ENABLED_BROKERS: ibkr
      DEFAULT_BROKER: ibkr
      ENGINE_BROKER: ibkr
      IBKR_HOST: ib-gateway
      IBKR_PORT: "4004"                    # image's socat paper port; live 4003
      DRY_RUN: "true"                      # arm deliberately, later
    command: python main.py --broker ibkr run
```

Division of labor after this:

| Leg | Runs on | Session owner |
|---|---|---|
| Robinhood | Render worker | auth-service box (`GET /token`) |
| Alpaca | Render worker | Alpaca API keys |
| IBKR | **ibkr-gateway box** | gnzsnz/ib-gateway + IBC on the same host |

The Render worker never learns an IBKR coordinate; the box never runs the
Robinhood or Alpaca legs. Same service-boundary discipline as
`auth-service/` in CLAUDE.md — the box is its own service, core-logic only
ever dialed it, and here core-logic doesn't even do that.

Security invariants:

- **No published ports; no firewall rule for 4001–4004.** Inspection is
  `gcloud compute ssh <box> -- -L 4004:localhost:4004`, mirroring how the
  auth box is reached from laptops.
- **Credentials via Secret Manager** — the compose env comes from the
  instance's secret-fetch, same as `env.prod` on the auth box. Nothing in
  this repo.
- **Paper until proven**: `TRADING_MODE: paper` + `DRY_RUN=true` must
  survive one Sunday maintenance window and one weekly re-auth unattended
  before either is flipped.
- **Unique `IBKR_CLIENT_ID` per attached process** if anything else ever
  dials the same gateway.

Operational rhythm: IB Key phone prompt roughly weekly (auto-restart covers
the daily cycle, not the weekly re-auth); Sunday windows mean
`ConnectionError` from the client is retryable, not fatal; a laptop TWS login
with the same paper username kicks the box's session — use a second paper
user or accept the auto-relogin.

## Library note

`ib_insync` was archived in 2024 (its author passed away); the maintained
drop-in fork is [`ib_async`](https://github.com/ib-api-reloaded/ib_async).
The lazy import in `ibkr_client.py` (`_load_ib_client`) now prefers `ib_async`
and falls back to `ib_insync` for older gateway boxes that still have it.
`scripts/connect_probe.py` is a read-only connectivity check (it imports
`ib_async` directly) — run it on the gateway box to confirm the socket before
enabling the broker.
