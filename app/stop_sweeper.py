#!/usr/bin/env python3
"""Daily trailing-stop sweeper with a local SQLite cache/queue.

One sweep at start of day:
  1. Read active trailing-stop orders from RH (through the auth-service) and
     mirror them into SQLite.
  2. For every ticker in the universe, ensure a percentage trailing stop
     (default 16%) exists — place one (dry_run by default) if missing.
  3. Compute expiry (RH cancels GTC orders after ~90 days) and flag stops
     expiring soon.

Between sweeps, other services read SQLite as the queue — RH is only
consulted when SQLite says a stop is about to expire (then we re-read and
renew via the replace/PUT path). If the DB is missing or empty, a sweep
re-populates it. A weekly reconciliation sweeper (SQLite vs RH book) comes
later.

Transport:
  --via proxy  (default) — the deployed Render API's /api/robinhood/* proxy;
               works from a laptop (the box's :443 only admits Render Ohio).
               Replace/renew is NOT exposed here (logged + skipped).
  --via box    — direct auth-service URL + bearer token; supports replace.
               Use when running on Render or the box itself.

Usage:
  python scripts/stop_sweeper.py sweep --tickers AAPL,MSFT [--live]
  python scripts/stop_sweeper.py check SYMBOL     # queue read (sqlite-first)
  python scripts/stop_sweeper.py list             # dump sqlite state
"""

import argparse
import json
import logging
import os
import sqlite3
import sys
import uuid
from datetime import datetime, timedelta, timezone

import requests

log = logging.getLogger("stop_sweeper")

DEFAULT_DB = os.getenv("STOP_DB_PATH", os.path.join(os.path.dirname(__file__), "..", "data", "stops.sqlite3"))
PROXY_BASE = os.getenv("RH_PROXY_BASE", "https://allocation-engine-api.onrender.com/api/robinhood")
BOX_BASE = os.getenv("AUTH_SERVICE_URL", "")
BOX_TOKEN = os.getenv("RH_AUTH_SERVICE_REQUEST_TOKEN", "")

TRAIL_PERCENT = float(os.getenv("STOP_TRAIL_PERCENT", "16"))
GTC_LIFETIME_DAYS = 90          # RH cancels GTC orders after ~90 days
EXPIRY_LEAD_DAYS = int(os.getenv("STOP_EXPIRY_LEAD_DAYS", "7"))


# --------------------------------------------------------------------------- #
# guardrails — this tool manages protective trailing stops, nothing else.
# Every payload passes validate_trailing_stop_payload() at the client layer,
# so no code path can submit a plain buy/sell/limit order through it.
# --------------------------------------------------------------------------- #

class GuardrailViolation(ValueError):
    """Payload or MCP call outside the allowed trailing-stop surface."""


_ALLOWED_PAYLOAD_KEYS = {
    "account", "instrument", "symbol", "type", "time_in_force", "trigger",
    "side", "quantity", "trailing_peg", "ref_id", "stop_price",
}

# MCP is read-only from this tool: market/portfolio reads are fine, anything
# that could move money is not.
MCP_READ_ONLY_TOOLS = {
    "get_positions", "get_portfolio", "get_accounts", "get_balances",
    "get_orders", "get_order_history", "get_quote", "get_quotes",
    "search_symbols", "get_watchlists",
}
_MCP_ALLOWED_METHODS = {"initialize", "notifications/initialized",
                        "tools/list", "tools/call", "ping"}


def validate_trailing_stop_payload(payload, live=False):
    """Reject anything that is not a percentage trailing-stop SELL order."""
    if not isinstance(payload, dict):
        raise GuardrailViolation("payload must be a dict")
    unknown = set(payload) - _ALLOWED_PAYLOAD_KEYS
    if unknown:
        raise GuardrailViolation(f"unexpected payload keys: {sorted(unknown)}")
    if payload.get("trigger") != "stop":
        raise GuardrailViolation("trigger must be 'stop' (no immediate orders)")
    if payload.get("side") != "sell":
        raise GuardrailViolation("side must be 'sell' — protective stops only")
    if payload.get("type") != "market":
        raise GuardrailViolation("type must be 'market' (no limit orders)")
    if payload.get("time_in_force") != "gtc":
        raise GuardrailViolation("time_in_force must be 'gtc'")
    peg = payload.get("trailing_peg")
    if not isinstance(peg, dict) or peg.get("type") != "percentage":
        raise GuardrailViolation("trailing_peg.type must be 'percentage'")
    try:
        pct = float(peg.get("percentage"))
    except (TypeError, ValueError):
        raise GuardrailViolation("trailing_peg.percentage must be numeric")
    if not 0 < pct <= 50:
        raise GuardrailViolation(f"trail percentage {pct} outside (0, 50]")
    try:
        qty = float(payload.get("quantity"))
    except (TypeError, ValueError):
        raise GuardrailViolation("quantity must be numeric")
    if qty <= 0:
        raise GuardrailViolation("quantity must be positive")
    if live and (not payload.get("account") or not payload.get("instrument")):
        raise GuardrailViolation(
            "live placement requires account and instrument URLs")
    return payload


def validate_mcp_call(payload):
    """Allow only read-only MCP traffic from this tool."""
    if not isinstance(payload, dict):
        raise GuardrailViolation("MCP payload must be a dict")
    method = payload.get("method")
    if method not in _MCP_ALLOWED_METHODS:
        raise GuardrailViolation(f"MCP method '{method}' not allowed")
    if method == "tools/call":
        tool = (payload.get("params") or {}).get("name", "")
        if tool not in MCP_READ_ONLY_TOOLS:
            raise GuardrailViolation(
                f"MCP tool '{tool}' is not read-only — blocked")
    return payload


# --------------------------------------------------------------------------- #
# transports
# --------------------------------------------------------------------------- #

class ProxyClient:
    """Talk to RH through the deployed Render proxy (works off-network)."""

    def __init__(self, base=PROXY_BASE, timeout=45):
        self.base = base.rstrip("/")
        self.timeout = timeout

    def get_stops(self):
        r = requests.get(f"{self.base}/trailing-stop", timeout=self.timeout)
        r.raise_for_status()
        return r.json().get("orders", [])

    def place_stop(self, payload, dry_run=True):
        validate_trailing_stop_payload(payload, live=not dry_run)
        r = requests.post(f"{self.base}/trailing-stop",
                          json={"payload": payload, "dry_run": dry_run},
                          timeout=self.timeout)
        r.raise_for_status()
        return r.json()

    def replace_stop(self, order_id, payload, dry_run=True):
        raise NotImplementedError(
            "replace is not exposed via the Render proxy — run with --via box")

    def mcp_call(self, payload):
        validate_mcp_call(payload)
        r = requests.post(f"{self.base}/mcp", json={"payload": payload},
                          timeout=self.timeout)
        r.raise_for_status()
        return r.json()


class BoxClient:
    """Talk to the auth-service directly (Render/box network only)."""

    def __init__(self, base=BOX_BASE, token=BOX_TOKEN, timeout=45):
        if not base or not token:
            raise SystemExit("AUTH_SERVICE_URL / RH_AUTH_SERVICE_REQUEST_TOKEN not set")
        self.base = base.rstrip("/")
        self.headers = {"Authorization": f"Bearer {token}"}
        self.timeout = timeout

    def get_stops(self):
        r = requests.get(f"{self.base}/orders/trailing_stop",
                         headers=self.headers, timeout=self.timeout)
        r.raise_for_status()
        return r.json().get("orders", [])

    def place_stop(self, payload, dry_run=True):
        validate_trailing_stop_payload(payload, live=not dry_run)
        r = requests.post(f"{self.base}/orders/trailing_stop",
                          json={"payload": payload, "dry_run": dry_run},
                          headers=self.headers, timeout=self.timeout)
        r.raise_for_status()
        return r.json()

    def replace_stop(self, order_id, payload, dry_run=True):
        validate_trailing_stop_payload(payload, live=not dry_run)
        if not order_id:
            raise GuardrailViolation("replace requires an existing order_id")
        r = requests.post(f"{self.base}/orders/trailing_stop/replace",
                          json={"order_id": order_id, "payload": payload,
                                "dry_run": dry_run},
                          headers=self.headers, timeout=self.timeout)
        r.raise_for_status()
        return r.json()

    def mcp_call(self, payload):
        validate_mcp_call(payload)
        r = requests.post(f"{self.base}/exec/mcp", json={"payload": payload},
                          headers=self.headers, timeout=self.timeout)
        r.raise_for_status()
        return r.json()


# --------------------------------------------------------------------------- #
# sqlite store (the queue other services read)
# --------------------------------------------------------------------------- #

SCHEMA = """
CREATE TABLE IF NOT EXISTS stops (
  symbol        TEXT PRIMARY KEY,
  order_id      TEXT,
  state         TEXT,
  trail_percent REAL,
  quantity      TEXT,
  side          TEXT,
  created_at    TEXT,
  expires_at    TEXT,
  last_synced   TEXT,
  raw           TEXT
);
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT);
"""


class StopStore:
    def __init__(self, path=DEFAULT_DB):
        os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
        self.path = path
        self.db = sqlite3.connect(path)
        self.db.row_factory = sqlite3.Row
        self.db.executescript(SCHEMA)

    def upsert(self, symbol, order):
        created = order.get("created_at") or _now_iso()
        expires = _plus_days(created, GTC_LIFETIME_DAYS)
        peg = order.get("trailing_peg") or {}
        self.db.execute(
            """INSERT INTO stops(symbol, order_id, state, trail_percent, quantity,
                                 side, created_at, expires_at, last_synced, raw)
               VALUES(?,?,?,?,?,?,?,?,?,?)
               ON CONFLICT(symbol) DO UPDATE SET
                 order_id=excluded.order_id, state=excluded.state,
                 trail_percent=excluded.trail_percent, quantity=excluded.quantity,
                 side=excluded.side, created_at=excluded.created_at,
                 expires_at=excluded.expires_at, last_synced=excluded.last_synced,
                 raw=excluded.raw""",
            (symbol, order.get("id"), order.get("state"),
             float(peg.get("percentage") or 0) or None,
             order.get("quantity"), order.get("side"),
             created, expires, _now_iso(), json.dumps(order)))
        self.db.commit()

    def get(self, symbol):
        row = self.db.execute("SELECT * FROM stops WHERE symbol=?", (symbol,)).fetchone()
        return dict(row) if row else None

    def all(self):
        return [dict(r) for r in self.db.execute("SELECT * FROM stops ORDER BY symbol")]

    def prune_missing(self, live_symbols):
        rows = self.db.execute("SELECT symbol FROM stops").fetchall()
        gone = [r["symbol"] for r in rows if r["symbol"] not in live_symbols]
        for s in gone:
            self.db.execute("DELETE FROM stops WHERE symbol=?", (s,))
        if gone:
            self.db.commit()
        return gone

    def set_meta(self, key, value):
        self.db.execute("INSERT INTO meta(key,value) VALUES(?,?) "
                        "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                        (key, value))
        self.db.commit()

    def get_meta(self, key):
        row = self.db.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
        return row["value"] if row else None

    def swept_today(self):
        last = self.get_meta("last_sweep_at")
        return bool(last) and last[:10] == _now_iso()[:10]


def _now_iso():
    return datetime.now(timezone.utc).isoformat()


def _plus_days(iso, days):
    try:
        dt = datetime.fromisoformat(iso.replace("Z", "+00:00"))
    except ValueError:
        dt = datetime.now(timezone.utc)
    return (dt + timedelta(days=days)).isoformat()


def _expiring_soon(row, lead_days=EXPIRY_LEAD_DAYS):
    if not row or not row.get("expires_at"):
        return False
    exp = datetime.fromisoformat(row["expires_at"])
    return exp - datetime.now(timezone.utc) <= timedelta(days=lead_days)


# --------------------------------------------------------------------------- #
# sweep + queue logic
# --------------------------------------------------------------------------- #

def build_payload(symbol, side, quantity, trail_percent,
                  account_url="", instrument_url=""):
    """trailing_peg percentage payload (matches the auth-service builder).

    account/instrument URLs are required by RH for live placement; when empty
    (local dry-run) the box still logs/echoes the payload without sending.
    """
    return {
        "account": account_url,
        "instrument": instrument_url,
        "symbol": symbol,
        "type": "market",
        "time_in_force": "gtc",
        "trigger": "stop",
        "side": side,
        "quantity": str(quantity),
        "trailing_peg": {"type": "percentage", "percentage": str(trail_percent)},
        "ref_id": str(uuid.uuid4()),
    }


def _symbol_of(order):
    return (order.get("symbol") or order.get("chain_symbol") or "").upper()


def sweep(client, store, tickers, trail_percent=TRAIL_PERCENT, dry_run=True):
    """The start-of-day pass: mirror RH stops into sqlite, cover naked tickers."""
    log.info("sweep: reading active trailing stops from RH")
    orders = client.get_stops()
    covered = {}
    for o in orders:
        sym = _symbol_of(o)
        if sym:
            covered[sym] = o
            store.upsert(sym, o)
    pruned = store.prune_missing(set(covered) | {t.upper() for t in tickers})
    if pruned:
        log.info("sweep: pruned stale rows: %s", pruned)

    placed, renewed = [], []
    for t in (x.upper() for x in tickers):
        row = store.get(t)
        if t not in covered:
            log.info("sweep: %s has no active stop — placing %.0f%% trailing stop"
                     " (dry_run=%s)", t, trail_percent, dry_run)
            payload = build_payload(t, "sell", 1, trail_percent)
            result = client.place_stop(payload, dry_run=dry_run)
            placed.append({"symbol": t, "result": result})
            store.upsert(t, {"id": None, "state": "dry_run" if dry_run else "submitted",
                             "created_at": _now_iso(), "side": "sell",
                             "quantity": "1",
                             "trailing_peg": {"type": "percentage",
                                              "percentage": str(trail_percent)}})
        elif _expiring_soon(store.get(t)):
            renewed.append(renew(client, store, t, dry_run=dry_run))

    store.set_meta("last_sweep_at", _now_iso())
    return {"active_from_rh": len(covered), "placed": placed,
            "renewed": renewed, "pruned": pruned}


def renew(client, store, symbol, dry_run=True):
    """Stop is near expiry: confirm against RH, then replace (PUT) to renew."""
    log.info("renew: %s near expiry — re-checking RH book", symbol)
    live = {_symbol_of(o): o for o in client.get_stops()}
    order = live.get(symbol.upper())
    if not order:
        log.warning("renew: %s not in RH book — will be re-placed next sweep", symbol)
        return {"symbol": symbol, "action": "missing_in_rh"}
    payload = build_payload(
        symbol, order.get("side", "sell"), order.get("quantity", "1"),
        (order.get("trailing_peg") or {}).get("percentage", TRAIL_PERCENT),
        account_url=order.get("account", ""),
        instrument_url=order.get("instrument", ""))
    try:
        result = client.replace_stop(order["id"], payload, dry_run=dry_run)
    except (NotImplementedError, GuardrailViolation) as e:
        log.warning("renew: %s", e)
        return {"symbol": symbol, "action": "renew_skipped", "reason": str(e)}
    # A live replace yields a NEW order (fresh created_at -> fresh expiry);
    # mirror that, or expires_at never advances and we renew forever.
    if not dry_run and isinstance(result, dict) and result.get("created_at"):
        store.upsert(symbol.upper(), result)
    else:
        store.upsert(symbol.upper(), order)
    return {"symbol": symbol, "action": "renewed", "result": result}


def check(client, store, symbol):
    """Queue read for other services: sqlite-first, RH only when near expiry."""
    row = store.get(symbol.upper())
    if row is None and not store.swept_today():
        log.info("check: sqlite empty/stale for %s — repopulating via sweep", symbol)
        sweep(client, store, [symbol])
        row = store.get(symbol.upper())
    if row and _expiring_soon(row):
        log.info("check: %s expiring soon -> explicit RH check + renew", symbol)
        renew(client, store, symbol)
        row = store.get(symbol.upper())
    if row:
        row["expiring_soon"] = _expiring_soon(row)
        row.pop("raw", None)
    return row


# --------------------------------------------------------------------------- #
# cli
# --------------------------------------------------------------------------- #

def main():
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)-7s %(name)s  %(message)s",
                        datefmt="%H:%M:%S")
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("command", choices=["sweep", "check", "list"])
    ap.add_argument("symbol", nargs="?", help="symbol for 'check'")
    ap.add_argument("--tickers", default="", help="comma-separated universe for sweep")
    ap.add_argument("--via", choices=["proxy", "box"], default="proxy")
    ap.add_argument("--live", action="store_true", help="disable dry_run (real orders)")
    ap.add_argument("--db", default=DEFAULT_DB)
    args = ap.parse_args()

    client = BoxClient() if args.via == "box" else ProxyClient()
    store = StopStore(args.db)

    if args.command == "sweep":
        tickers = [t for t in args.tickers.split(",") if t.strip()]
        out = sweep(client, store, tickers, dry_run=not args.live)
    elif args.command == "check":
        if not args.symbol:
            ap.error("check requires a symbol")
        out = check(client, store, args.symbol)
    else:
        out = {"db": os.path.abspath(args.db), "rows": store.all(),
               "last_sweep_at": store.get_meta("last_sweep_at")}

    print(json.dumps(out, indent=2, default=str))


if __name__ == "__main__":
    main()
