# Interactive Brokers (IBKR) Setup

## Overview

The IBKR broker places **live options orders headless** via the IBKR **Client Portal Web API** authenticated with **OAuth 1.0a**. This flow is chosen specifically because it fits Render:

- **No Gateway process.** Unlike the older Client Portal Gateway (a Java process you must keep running and re-authenticate through a browser), OAuth 1.0a mints a brokerage session purely from stored secrets. There is nothing to babysit on the box.
- **Re-minted from secrets each ~24h.** The live session token expires roughly daily; it is regenerated automatically from the consumer key + RSA keys + access token, with no human in the loop (contrast Robinhood, which can require a device challenge).
- **Headless and self-contained.** All credentials are env vars; the app derives the session at runtime.

We use the [`ibind`](https://github.com/Voyz/ibind) Python library, which implements the OAuth 1.0a handshake end to end: the RSA/Diffie-Hellman **live session token** computation, `ssodh/init` to open the brokerage session, and the `tickle` keepalive. We do not have to implement the cryptographic handshake ourselves.

## One-time IBKR setup

1. Access the IBKR **OAuth Self-Service Portal** and log in with the **username that will be used for API sessions** (the trading account's login, not a secondary user).
2. **Register an OAuth consumer.** On registration you obtain the **consumer key** and the **Diffie-Hellman prime** for this consumer. Record both.
3. **Generate two RSA key pairs** — one for **signature** and one for **encryption** — using **`RSA-SHA256`**. Upload the **public** keys to the portal; keep the **private** PEMs securely (these become the `IBKR_SIGNATURE_KEY` and `IBKR_ENCRYPTION_KEY` secrets).
4. **Obtain the access token and access token secret** for the consumer. These authorize the consumer to act on the account.
5. **Note the account id.** Start with the **paper** account id; capture the **live** account id separately for when you go live.

## Secrets

Set these as env vars on each environment (see [Where to provision](#where-to-provision)). PEM-valued secrets must preserve newlines (multi-line env var).

| Env var | What it is | Where to set |
|---------|------------|--------------|
| `IBKR_ACCOUNT_ID` | IBKR account id used for orders (paper id first, live id later) | Render worker + api |
| `IBKR_PAPER` | `true` to target the paper environment, `false` for live | Render worker + api |
| `IBKR_CONSUMER_KEY` | OAuth consumer key from the Self-Service Portal | Render worker + api |
| `IBKR_ACCESS_TOKEN` | OAuth access token for the consumer | Render worker + api |
| `IBKR_ACCESS_TOKEN_SECRET` | OAuth access token secret | Render worker + api |
| `IBKR_DH_PRIME` | Diffie-Hellman prime for the consumer | Render worker + api |
| `IBKR_SIGNATURE_KEY` | **RSA private key PEM contents** for signing (newlines preserved) | Render worker + api |
| `IBKR_ENCRYPTION_KEY` | **RSA private key PEM contents** for encryption (newlines preserved) | Render worker + api |
| `IBKR_MAX_OPTION_ORDER_QTY` | Hard cap on contracts per option order (safety limit) | Render worker + api |
| `ENGINE_BROKER` | Set to `ibkr` to route the engine through the IBKR broker | Render worker + api |
| `DRY_RUN` | `true` simulates order placement; `false` places real orders | Render worker + api |

> The two PEM secrets (`IBKR_SIGNATURE_KEY`, `IBKR_ENCRYPTION_KEY`) hold the **full PEM contents**, including the `-----BEGIN/END ... KEY-----` lines, with newlines intact. The app does not read key files from disk directly — `Config.ibkr_key_files()` writes these values to temp files at runtime and hands the paths to `ibind`.

## Where to provision

Provision the secrets above on **both Render services**, since either may construct the IBKR broker:

| Service | ID | Type |
|---------|-----|------|
| allocation-engine-2.0 | `srv-d6i9evua2pns73901hj0` | worker (engine loop) |
| allocation-engine-api | `srv-d6sbe2k50q8c73fgn86g` | web (`/api/*` endpoints) |

If the **Netlify** build/runtime touches these values (e.g. a front end or function that calls the order endpoint), set the same env vars there too. Otherwise Netlify can be skipped.

The PEM-valued secrets go in as **multi-line** env vars (paste the whole PEM block). Do not base64-encode or collapse them to one line — `Config.ibkr_key_files()` expects literal PEM contents with preserved newlines and materializes them to temp files at runtime.

## Going live

Roll out **paper-first**, then flip to live:

1. **Paper, dry-run:** `IBKR_PAPER=true`, `ENGINE_BROKER=ibkr`, `DRY_RUN=true`. The engine routes through IBKR but only simulates orders. Confirm the session mints and `tickle` keepalive succeeds in the logs.
2. **Paper, real (paper) orders:** flip `DRY_RUN=false`. Orders now hit the IBKR paper account. Verify with a small **far-OTM** contract via `POST /api/options/order/ibkr` and confirm it appears in the paper account.
3. **Live:** once paper is verified, set `IBKR_PAPER=false` and swap `IBKR_ACCOUNT_ID` to the live account id. Keep `IBKR_MAX_OPTION_ORDER_QTY` conservative on the first live runs.

## Session lifecycle

- The OAuth **live session token** is valid for ~24h.
- `ibind` keeps the brokerage session warm with periodic **`tickle`** calls and opens it via **`ssodh/init`**.
- On expiry, the session is **re-minted automatically** from the stored secrets (consumer key + RSA keys + access token/secret + DH prime). There is **no human step** — unlike the Robinhood device-challenge path, IBKR OAuth needs no email approval or device trust, so a fresh deploy or a 24h rollover self-heals.

## References

- IBKR Campus — [OAuth 1.0a Extended](https://www.interactivebrokers.com/campus/ibkr-api-page/oauth-1-0a-extended/)
- IBKR Campus — [Web API v1.0 (CP API)](https://www.interactivebrokers.com/campus/ibkr-api-page/cpapi-v1/)
- IBKR Campus — [Trading Web API](https://www.interactivebrokers.com/campus/ibkr-api-page/trading/)
- [`ibind` library](https://github.com/Voyz/ibind) and its [OAuth 1.0a wiki](https://github.com/Voyz/ibind/wiki/OAuth-1.0a)
