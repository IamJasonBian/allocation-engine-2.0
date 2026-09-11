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
re-populates it.

`reconcile` is the weekly pass over the same book: it records the high-water
stop level per symbol and reports where the live orders have drifted below it
(see docs/TRAILING_STOP_HIGH_WATER.md). It is read-only against RH — it
writes only local marks and never touches an order.

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
  python scripts/stop_sweeper.py reconcile        # high-water marks vs RH
  python scripts/stop_sweeper.py hw-reset SYMBOL --reason filled
"""

import argparse
import json
import logging
import math
import os
import sqlite3
import sys
import time
import uuid
from datetime import datetime, timedelta, timezone

import requests

log = logging.getLogger("stop_sweeper")

DEFAULT_DB = os.getenv("STOP_DB_PATH", os.path.join(os.path.dirname(__file__), "..", "data", "stops.sqlite3"))
PROXY_BASE = os.getenv("RH_PROXY_BASE", "https://allocation-engine-api.onrender.com/api/robinhood")
BOX_BASE = os.getenv("AUTH_SERVICE_URL", "")
BOX_TOKEN = os.getenv("RH_AUTH_SERVICE_REQUEST_TOKEN", "")

TRAIL_PERCENT = float(os.getenv("STOP_TRAIL_PERCENT", "16"))
# Vol-scaled trail bounds (docs/TRAILING_STOP_WATERFALL.md): clamp then
# renormalize so the budget invariant survives; quantize to broker-friendly steps.
TRAIL_FLOOR = 8.0
TRAIL_CAP = 24.0
TRAIL_QUANTUM = 0.5
GTC_LIFETIME_DAYS = 90          # RH cancels GTC orders after ~90 days
EXPIRY_LEAD_DAYS = int(os.getenv("STOP_EXPIRY_LEAD_DAYS", "7"))
# Pace live placements — RH throttles bursts (~429 after a handful/second).
PLACE_DELAY_SECONDS = float(os.getenv("STOP_PLACE_DELAY_SECONDS", "1.5"))


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
-- High-water stop levels. Deliberately NOT a column on `stops`: that table is
-- pruned when a symbol leaves the RH book (prune_missing), and a mark has to
-- outlive the order it was observed from. hw_stop is nullable so a reset keeps
-- the row as an audit trail instead of erasing why the mark went away.
CREATE TABLE IF NOT EXISTS stop_high_water (
  symbol       TEXT PRIMARY KEY,
  hw_stop      REAL,
  source       TEXT,
  observed_at  TEXT,
  reset_reason TEXT,
  reset_at     TEXT
);
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

    # --- high-water marks (see docs/TRAILING_STOP_HIGH_WATER.md) ---------- #

    def hw_get(self, symbol):
        row = self.db.execute("SELECT * FROM stop_high_water WHERE symbol=?",
                              (symbol.upper(),)).fetchone()
        return dict(row) if row else None

    def hw_all(self):
        return [dict(r) for r in self.db.execute(
            "SELECT * FROM stop_high_water ORDER BY symbol")]

    def hw_observe(self, symbol, level, source="rh_order"):
        """Raise a symbol's high-water stop. Monotone: never lowers it.

        Returns (hw_stop, raised); raised is True when this observation moved
        the mark. A non-numeric or non-positive level is ignored rather than
        written — a bad read must not be able to establish a floor.
        """
        sym = symbol.upper()
        try:
            level = float(level)
        except (TypeError, ValueError):
            return (self.hw_get(sym) or {}).get("hw_stop"), False
        if level <= 0:
            return (self.hw_get(sym) or {}).get("hw_stop"), False
        current = (self.hw_get(sym) or {}).get("hw_stop")
        if current is not None and float(current) >= level:
            return float(current), False
        self.db.execute(
            """INSERT INTO stop_high_water(symbol, hw_stop, source, observed_at)
               VALUES(?,?,?,?)
               ON CONFLICT(symbol) DO UPDATE SET
                 hw_stop=excluded.hw_stop, source=excluded.source,
                 observed_at=excluded.observed_at""",
            (sym, level, source, _now_iso()))
        self.db.commit()
        return level, True

    def hw_reset(self, symbol, reason):
        """Invalidate a mark (fill, re-entry, corporate action).

        The row survives with the reason stamped: a mark that vanished without
        explanation is indistinguishable from one that was never established.
        """
        sym = symbol.upper()
        self.db.execute(
            """INSERT INTO stop_high_water(symbol, hw_stop, source, observed_at,
                                           reset_reason, reset_at)
               VALUES(?,NULL,NULL,NULL,?,?)
               ON CONFLICT(symbol) DO UPDATE SET
                 hw_stop=NULL, source=NULL, observed_at=NULL,
                 reset_reason=excluded.reset_reason, reset_at=excluded.reset_at""",
            (sym, reason, _now_iso()))
        self.db.commit()
        return self.hw_get(sym)


def _now_iso():
    return datetime.now(timezone.utc).isoformat()


def _parse_utc(iso):
    """Parse an ISO timestamp to a UTC-aware datetime, or None if unparseable.

    Robinhood stamps `created_at` with a trailing ``Z``; other sources may emit
    a naive timestamp with no offset. A naive value is assumed to be UTC so
    downstream arithmetic never mixes offset-naive and offset-aware datetimes
    (which raises TypeError).
    """
    if not iso:
        return None
    try:
        dt = datetime.fromisoformat(str(iso).replace("Z", "+00:00"))
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _plus_days(iso, days):
    dt = _parse_utc(iso) or datetime.now(timezone.utc)
    return (dt + timedelta(days=days)).isoformat()


def _expiring_soon(row, lead_days=EXPIRY_LEAD_DAYS):
    if not row or not row.get("expires_at"):
        return False
    exp = _parse_utc(row["expires_at"])
    if exp is None:
        return False
    return exp - datetime.now(timezone.utc) <= timedelta(days=lead_days)


# --------------------------------------------------------------------------- #
# sweep + queue logic
# --------------------------------------------------------------------------- #

def initial_stop_price(current_price, trail_percent, side="sell"):
    """Initial trigger price RH requires even for a trailing stop.

    Sell stop sits trail_percent BELOW the current price; from there RH
    trails it upward. Verified live: without this RH rejects the order with
    "Stop limit order requested, but no stop price provided."
    """
    pct = float(trail_percent) / 100.0
    factor = (1 - pct) if side == "sell" else (1 + pct)
    return round(float(current_price) * factor, 2)


def build_payload(symbol, side, quantity, trail_percent,
                  account_url="", instrument_url="", current_price=None):
    """Percentage trailing-stop payload (trailing_peg + initial stop_price).

    account/instrument URLs are required by RH for live placement; when empty
    (local dry-run) the box still logs/echoes the payload without sending.
    """
    payload = {
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
    if current_price:
        payload["stop_price"] = str(initial_stop_price(current_price, trail_percent, side))
    return payload


def _symbol_of(order):
    return (order.get("symbol") or order.get("chain_symbol") or "").upper()


def resolve_instrument_url(symbol):
    """Resolve a ticker to its RH instrument URL (public endpoint, no auth)."""
    try:
        r = requests.get("https://api.robinhood.com/instruments/",
                         params={"symbol": symbol.upper()}, timeout=15)
        r.raise_for_status()
        results = r.json().get("results") or []
        return results[0].get("url", "") if results else ""
    except Exception as e:  # noqa: BLE001
        log.warning("instrument lookup failed for %s: %s", symbol, e)
        return ""


def account_url_from_box():
    """Derive the RH account URL from the box token cached in sqlite."""
    try:
        from app.box_session import get_cached_token
        n = (get_cached_token() or {}).get("account_number", "")
        return f"https://api.robinhood.com/accounts/{n}/" if n else ""
    except Exception:  # noqa: BLE001
        return ""


def _placement_ok(result):
    """A real RH placement carries an order id; a rejection carries an error."""
    if not isinstance(result, dict):
        return False, "non-dict response"
    if result.get("dry_run"):
        return True, "dry_run"
    if result.get("id"):
        return True, result.get("state") or "submitted"
    # RH rejections come back (HTTP 200 from the box) as an error envelope.
    detail = (result.get("non_field_errors") or result.get("detail")
              or result.get("error") or result)
    return False, str(detail)[:200]


def compute_trail_percents(market_values, sigmas, budget=TRAIL_PERCENT,
                           floor=TRAIL_FLOOR, cap=TRAIL_CAP,
                           quantum=TRAIL_QUANTUM):
    """Vol-scaled trailing-stop %% per symbol under the fixed risk budget.

    Pure function (no I/O): trail_i = budget * sigma_i / sigma_bar_w, where
    sigma_bar_w is the market-value-weighted mean sigma, so the weighted
    average trail equals `budget` — the flat-percent risk profile is frozen
    and only the per-symbol distribution moves. Symbols with degenerate data
    (missing/zero sigma or market value) fall back to the flat `budget`,
    which keeps them invariant-neutral. Sigmas only enter as ratios, so any
    consistent unit (daily or annualized vol) works.

    Args:
        market_values: symbol -> market value (weight basis).
        sigmas: symbol -> realized volatility; missing entries fall back flat.
        budget: weighted-average trail to preserve (default flat 16).
        floor: minimum per-symbol trail %% (clamped, then renormalized).
        cap: maximum per-symbol trail %% (clamped, then renormalized).
        quantum: quantize trails to this step; 0 disables quantization.

    Returns:
        symbol (uppercased) -> trail percent.
    """
    out, scaled = {}, {}
    for sym, mv in (market_values or {}).items():
        try:
            mv = float(mv or 0)
            sig = float((sigmas or {}).get(sym) or 0)
        except (TypeError, ValueError):
            mv, sig = 0.0, 0.0
        if mv > 0 and sig > 1e-12:
            scaled[sym.upper()] = (mv, sig)
        else:
            out[sym.upper()] = float(budget)
            log.info("trail: %s degenerate data (mv=%s, sigma=%s) — flat %.1f%%",
                     sym, mv, (sigmas or {}).get(sym), budget)

    if scaled:
        total_mv = sum(mv for mv, _ in scaled.values())
        w = {s: mv / total_mv for s, (mv, _) in scaled.items()}
        sigma_bar = sum(w[s] * sig for s, (_, sig) in scaled.items())
        raw = {s: budget * sig / sigma_bar for s, (_, sig) in scaled.items()}
        # Water-filling: pin the worst clamp violator, rescale the rest so the
        # invariant survives, repeat. One pin per pass — pinning both sides at
        # once can leave no freedom to renormalize (opposite-direction
        # violators may self-heal once the other side is pinned).
        fixed, free = {}, dict(raw)
        while free:
            residual = budget - sum(w[s] * t for s, t in fixed.items())
            k = residual / sum(w[s] * raw[s] for s in free)
            trial = {s: raw[s] * k for s in free}
            viol = {s: max(t - cap, floor - t) for s, t in trial.items()
                    if not floor <= t <= cap}
            if not viol:
                free = trial
                break
            worst = max(viol, key=viol.get)
            fixed[worst] = min(max(trial[worst], floor), cap)
            del free[worst]
        trails = {**fixed, **free}
        drift = sum(w[s] * t for s, t in trails.items()) - budget
        if abs(drift) > 0.1:
            log.warning("trail: invariant drift %.3f%% after clamping — "
                        "check clamp bounds vs budget", drift)
        out.update(trails)

    if quantum:
        out = {s: round(round(t / quantum) * quantum, 4) for s, t in out.items()}
    return out


# --------------------------------------------------------------------------- #
# high-water stop levels — reading the level off the RH book
# --------------------------------------------------------------------------- #

# An RH percentage peg ratchets, but only for the lifetime of one order; every
# rewrite re-anchors to the mark at rewrite time. Which field on a live order
# carries the ratcheted trigger is NOT confirmed (Phase 1 in
# docs/TRAILING_STOP_HIGH_WATER.md), so probe the candidates in preference
# order and report which one answered — a live `reconcile` run settles the
# question instead of a guess baked into the write path.
_STOP_LEVEL_PATHS = (
    ("stop_price",),
    ("stop_trigger_price",),
    ("trigger_price",),
    ("trailing_peg", "price"),
    ("trailing_peg", "stop_price"),
)


def stop_level_of(order):
    """Current trigger level of an RH trailing-stop order.

    Returns (level, source_field); (None, "") when no candidate field carries
    a usable number. Never guesses a level from the peg percentage — that is
    the caller's fallback to make explicitly (see read_stop_levels' derived
    path), because a derived level is materially weaker evidence.
    """
    for path in _STOP_LEVEL_PATHS:
        node = order
        for key in path:
            node = (node or {}).get(key) if isinstance(node, dict) else None
        if node in (None, ""):
            continue
        try:
            level = float(node)
        except (TypeError, ValueError):
            continue
        if level > 0:
            return level, ".".join(path)
    return None, ""


def read_stop_levels(orders):
    """Per-symbol view of the live trailing-stop book.

    symbol -> {level, source, order_id, trail_percent}. `level` is None when
    the order exposes no trigger level; `trail_percent` comes off the peg so
    a caller can derive one from a price.
    """
    out = {}
    for o in orders or []:
        sym = _symbol_of(o)
        if not sym:
            continue
        level, source = stop_level_of(o)
        try:
            pct = float((o.get("trailing_peg") or {}).get("percentage"))
        except (TypeError, ValueError):
            pct = None
        out[sym] = {"level": level, "source": source, "order_id": o.get("id"),
                    "trail_percent": pct}
    return out


def apply_high_water(price, trail_percent, hw_stop, quantum=TRAIL_QUANTUM):
    """Floor the percentage-derived stop at the high-water level.

    RH only accepts a percentage peg (validate_trailing_stop_payload), so the
    level is expressed as a back-solved percentage plus an explicit
    stop_price. The peg is quantized DOWN, because a smaller peg sits the stop
    higher — rounding the other way would place it under the mark it is meant
    to floor.

    Returns {stop_price, peg_percent, binding, tight, breach}:
      binding — the high-water floor moved the stop above the %-derived level.
      tight   — the resulting peg is below TRAIL_FLOOR. The floor wins anyway:
                honouring TRAIL_FLOOR here would lower a protective level that
                was already earned, which is the exact failure this exists to
                prevent. Reported so it stays visible.
      breach  — price has fallen to or through the mark, so no placeable peg
                exists. Callers must NOT re-anchor lower to get an order out.
    """
    flat = {"stop_price": None, "peg_percent": None, "binding": False,
            "tight": False, "breach": True}
    try:
        price = float(price)
        pct = float(trail_percent)
    except (TypeError, ValueError):
        return flat
    if price <= 0 or not 0 < pct <= 50:
        return flat

    base = initial_stop_price(price, pct)
    try:
        hw = float(hw_stop) if hw_stop is not None else 0.0
    except (TypeError, ValueError):
        hw = 0.0
    target = max(base, hw)
    if target >= price:
        return flat

    peg = (1 - target / price) * 100
    if quantum:
        peg = round((peg // quantum) * quantum, 4)
    if not 0 < peg <= 50:
        # Within one quantum of the price (or beyond the hard guardrail):
        # unplaceable, and rounding up would drop the stop below the mark.
        return flat
    stop = round(price * (1 - peg / 100), 2)
    if hw and stop < hw:
        # Rounding to the cent can shave a fraction off the level; when the
        # mark is what we are honouring, round its way instead.
        stop = math.ceil(hw * 100) / 100
    return {"stop_price": stop, "peg_percent": peg, "binding": hw > base,
            "tight": peg < TRAIL_FLOOR, "breach": False}


def sweep(client, store, tickers, trail_percent=TRAIL_PERCENT, dry_run=True,
          qty_map=None, price_map=None, account_url="",
          instrument_resolver=resolve_instrument_url, trail_map=None):
    """The start-of-day pass: mirror RH stops into sqlite, cover naked tickers.

    Live placement (dry_run=False) needs a WHOLE-share quantity (RH rejects
    fractional trailing stops), account/instrument URLs, and a current price
    (to set the initial stop). Anything unresolvable is skipped and logged;
    a placement is only counted 'placed' if RH returns an order id.
    """
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

    qty_map = {k.upper(): v for k, v in (qty_map or {}).items()}
    price_map = {k.upper(): v for k, v in (price_map or {}).items()}
    placed, renewed, skipped = [], [], []
    for t in (x.upper() for x in tickers):
        if t in covered:
            if _expiring_soon(store.get(t)):
                renewed.append(renew(client, store, t, dry_run=dry_run))
            continue

        instrument_url, current_price = "", price_map.get(t)
        # RH trailing stops are whole-share only.
        whole_qty = int(float(qty_map.get(t) or 0))
        if not dry_run:
            if whole_qty < 1:
                log.warning("sweep: %s skipped — no whole-share quantity "
                            "(have %s)", t, qty_map.get(t))
                skipped.append({"symbol": t, "reason": "fractional_or_no_qty"})
                continue
            if not current_price:
                log.warning("sweep: %s skipped — no current price for stop", t)
                skipped.append({"symbol": t, "reason": "no_price"})
                continue
            instrument_url = instrument_resolver(t)
            if not instrument_url or not account_url:
                log.warning("sweep: %s skipped — unresolved instrument/account "
                            "URL", t)
                skipped.append({"symbol": t, "reason": "unresolved_urls"})
                continue

        place_qty = whole_qty if not dry_run else (whole_qty or 1)
        t_trail = float((trail_map or {}).get(t, trail_percent))
        if not dry_run and (placed or skipped):
            time.sleep(PLACE_DELAY_SECONDS)   # pace to dodge RH 429 throttling
        log.info("sweep: %s placing %.1f%% trailing stop qty=%s (dry_run=%s)",
                 t, t_trail, place_qty, dry_run)
        payload = build_payload(t, "sell", place_qty, t_trail,
                                account_url=account_url,
                                instrument_url=instrument_url,
                                current_price=current_price)
        try:
            result = client.place_stop(payload, dry_run=dry_run)
        except GuardrailViolation as e:
            log.warning("sweep: %s blocked by guardrail: %s", t, e)
            skipped.append({"symbol": t, "reason": str(e)})
            continue

        ok, detail = _placement_ok(result)
        if not ok:
            log.warning("sweep: %s REJECTED by RH: %s", t, detail)
            skipped.append({"symbol": t, "reason": f"rh_rejected: {detail}"})
            continue
        placed.append({"symbol": t, "result": result})
        store.upsert(t, {"id": (result or {}).get("id"),
                         "state": "dry_run" if dry_run else detail,
                         "created_at": _now_iso(), "side": "sell",
                         "quantity": str(place_qty),
                         "trailing_peg": {"type": "percentage",
                                          "percentage": str(t_trail)}})

    store.set_meta("last_sweep_at", _now_iso())
    return {"active_from_rh": len(covered), "placed": placed,
            "renewed": renewed, "pruned": pruned, "skipped": skipped}


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
# reconciliation engine — local high-water marks vs the live RH book
# --------------------------------------------------------------------------- #

# Cents of slack before a level counts as having moved backwards; RH rounds
# trigger prices to the cent, so an exact comparison reports float noise.
HW_REGRESSION_TOLERANCE = 0.01


def reconcile(client, store, price_map=None, qty_map=None,
              trail_percent=TRAIL_PERCENT):
    """Compare the local high-water marks against RH and report the drift.

    Read-only against RH: it never places, replaces, or cancels an order. The
    only writes are to `stop_high_water`, and they are monotone — an
    observation can raise a mark, nothing here lowers one. Resets are a policy
    call, so orphans are *reported* as candidates rather than cleared.

    Level sources, in the order tried per symbol:
      1. the order's own trigger field (stop_level_of) — the real ratcheted
         level, if RH exposes one;
      2. `price × (1 − peg/100)` from price_map — a derived level. Because
         observations are monotone, repeated runs accumulate the peak, but a
         single run only sees today's mark, so findings from this source are
         flagged `verified: false`.

    Args:
        client: ProxyClient/BoxClient — only get_stops() is used.
        price_map: symbol -> current price. Without it, breaches cannot be
            detected and source 2 is unavailable.
        qty_map: symbol -> held quantity; separates "held, unprotected" from
            "position gone, stale mark".

    Returns {checked, observed, findings, reset_candidates, source_counts}.
    """
    price_map = {k.upper(): v for k, v in (price_map or {}).items()}
    qty_map = {k.upper(): v for k, v in (qty_map or {}).items()}

    log.info("reconcile: reading active trailing stops from RH")
    levels = read_stop_levels(client.get_stops())

    observed, findings, source_counts = [], [], {}
    for sym, info in sorted(levels.items()):
        level, source = info["level"], info["source"]
        pct = info["trail_percent"] or trail_percent
        if level is None:
            price = price_map.get(sym)
            if price and info["trail_percent"]:
                level, source = initial_stop_price(price, pct), "derived_peak"
        if level is None:
            findings.append({
                "symbol": sym, "finding": "no_level", "order_id": info["order_id"],
                "detail": "order exposes no trigger level and no price to "
                          "derive one from"})
            log.warning("reconcile: %s has no readable stop level", sym)
            continue

        source_counts[source] = source_counts.get(source, 0) + 1
        hw, raised = store.hw_observe(sym, level, source)
        observed.append({"symbol": sym, "level": level, "source": source,
                         "hw_stop": hw, "raised": raised})

        if hw is not None and level + HW_REGRESSION_TOLERANCE < hw:
            verified = source != "derived_peak"
            findings.append({
                "symbol": sym, "finding": "level_regression",
                "order_id": info["order_id"], "live_level": level,
                "hw_stop": hw, "give_back": round(hw - level, 2),
                "verified": verified})
            log.warning("reconcile: %s live stop %.2f is %.2f below the "
                        "high-water %.2f (verified=%s)",
                        sym, level, hw - level, hw, verified)

        price = price_map.get(sym)
        if price and hw is not None:
            plan = apply_high_water(price, pct, hw)
            if plan["breach"]:
                findings.append({
                    "symbol": sym, "finding": "hw_breach", "hw_stop": hw,
                    "price": float(price),
                    "detail": "price is at or through the high-water level — "
                              "no placeable peg; do not re-anchor lower"})
                log.warning("reconcile: %s BREACH — price %.2f vs high-water "
                            "%.2f", sym, float(price), hw)
            elif plan["binding"]:
                log.info("reconcile: %s high-water floor binds — %.2f vs "
                         "%.1f%% at %.2f", sym, hw, pct,
                         initial_stop_price(price, pct))

    reset_candidates = []
    for row in store.hw_all():
        sym = row["symbol"]
        if row.get("hw_stop") is None or sym in levels:
            continue
        held = float(qty_map.get(sym) or 0) > 0
        if held:
            findings.append({
                "symbol": sym, "finding": "missing_stop",
                "hw_stop": row["hw_stop"],
                "detail": "position held with a mark but no live stop in RH"})
            log.warning("reconcile: %s held with high-water %.2f but no live "
                        "stop", sym, row["hw_stop"])
        else:
            reset_candidates.append({
                "symbol": sym, "hw_stop": row["hw_stop"],
                "reason": "no live stop and no position — stale after exit"})
            findings.append({
                "symbol": sym, "finding": "orphan_high_water",
                "hw_stop": row["hw_stop"],
                "detail": "reset candidate; a stale mark blocks a re-entry"})

    store.set_meta("last_reconcile_at", _now_iso())
    log.info("reconcile: %d symbols in book, %d findings, sources=%s",
             len(levels), len(findings), source_counts or "none")
    return {"checked": len(levels), "observed": observed, "findings": findings,
            "reset_candidates": reset_candidates, "source_counts": source_counts}


# --------------------------------------------------------------------------- #
# options sweep — DRAFT (not wired into the engine loop yet)
# --------------------------------------------------------------------------- #

def sweep_options(client, store, option_positions, trail_percent=TRAIL_PERCENT,
                  dry_run=True):
    """DRAFT: protective-stop sweep for long option positions.

    Mirrors the equity sweep's shape (cover naked positions, sqlite-first
    queue) but options need a different order model than equity trailing
    stops, so for now this only surveys and records intent — it never places.

    option_positions: list from broker.options_positions() with at least
        {chain_symbol, option_id/option, quantity, type ('long'/'short'), ...}

    TODO(options-sweep):
      - RH options have no `trailing_peg`; a protective exit is a stop or
        stop-limit on the *option contract* (option_id), or a % move in the
        underlying. Decide the instrument: stop on the option leg vs. an
        underlying-triggered close. Confirm the payload against a live read
        the way we did for equities (initial stop_price was the missing key).
      - Only long positions (type == 'long', quantity > 0) get a protective
        sell-to-close; short options need buy-to-close and different risk.
      - Reuse client.get_stops() equivalent for options (needs an
        auth-service /orders/option_trailing_stop or MCP tool) to detect
        already-covered contracts before placing.
      - Expiry: option orders don't share the 90-day GTC lifetime; key the
        queue on option_id and reconcile against contract expiration instead.
      - Add guardrails: only sell-to-close / buy-to-close on held contracts,
        never opening new option exposure.
    """
    surveyed, todo = [], []
    for p in option_positions or []:
        sym = (p.get("chain_symbol") or p.get("symbol") or "").upper()
        qty = float(p.get("quantity", 0) or 0)
        if qty <= 0 or (p.get("type") or "long") != "long":
            continue
        contract = p.get("option") or p.get("option_id") or ""
        surveyed.append({"symbol": sym, "option_id": contract, "quantity": qty})
        # TODO(options-sweep): replace this with a real protective-order
        # placement once the option order model above is settled.
        todo.append(sym)
        log.info("[opt-sweep] would protect %s x%s (contract=%s) — NOT placed "
                 "(draft)", sym, qty, str(contract)[-12:])

    log.info("[opt-sweep] DRAFT survey: %d long option positions, "
             "placement not implemented (dry_run=%s)", len(surveyed), dry_run)
    return {"surveyed": surveyed, "todo_place": todo, "placed": []}


# --------------------------------------------------------------------------- #
# cli
# --------------------------------------------------------------------------- #

def _parse_pairs(raw):
    """Parse a SYM=VALUE,... CLI argument into {SYMBOL: float}."""
    out = {}
    for part in (raw or "").split(","):
        if "=" not in part:
            continue
        sym, _, val = part.partition("=")
        try:
            out[sym.strip().upper()] = float(val)
        except ValueError:
            continue
    return out


def main():
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)-7s %(name)s  %(message)s",
                        datefmt="%H:%M:%S")
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("command",
                    choices=["sweep", "check", "list", "reconcile", "hw-reset"])
    ap.add_argument("symbol", nargs="?", help="symbol for 'check' / 'hw-reset'")
    ap.add_argument("--reason", default="",
                    help="why the high-water mark is being reset (hw-reset)")
    ap.add_argument("--prices", default="",
                    help="reconcile: SYM=PRICE,... — enables breach detection "
                         "and the derived-level fallback")
    ap.add_argument("--qty", default="",
                    help="reconcile: SYM=QTY,... — separates a held position "
                         "with no stop from a stale mark after an exit")
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
    elif args.command == "reconcile":
        out = reconcile(client, store,
                        price_map=_parse_pairs(args.prices),
                        qty_map=_parse_pairs(args.qty))
    elif args.command == "hw-reset":
        if not args.symbol:
            ap.error("hw-reset requires a symbol")
        if not args.reason:
            ap.error("hw-reset requires --reason (the row keeps it for audit)")
        out = store.hw_reset(args.symbol, args.reason)
    else:
        out = {"db": os.path.abspath(args.db), "rows": store.all(),
               "high_water": store.hw_all(),
               "last_sweep_at": store.get_meta("last_sweep_at"),
               "last_reconcile_at": store.get_meta("last_reconcile_at")}

    print(json.dumps(out, indent=2, default=str))


if __name__ == "__main__":
    main()
