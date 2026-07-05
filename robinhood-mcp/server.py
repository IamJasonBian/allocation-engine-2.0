#!/usr/bin/env python3
"""Local Robinhood MCP — our logic in a simple box (stdlib only).

The official Robinhood MCP (agent.robinhood.com) requires an agentic-OAuth
token we can't mint headlessly, so this server exposes the same kind of tool
surface backed by our own session token instead. Speaks MCP over stdio
(newline-delimited JSON-RPC 2.0), so it plugs into Claude Code today
(`claude mcp add robinhood-local -- python3 server.py`) and can be relayed
from the auth-service box later.

Trade safety is structural: there are no buy/sell/cancel tools at all, and the
one write tool (place/replace trailing stop) builds its payload internally
from scalar arguments — a caller can never supply raw order JSON.

Token source (first match wins):
  RH_ACCESS_TOKEN          — a live RH bearer (e.g. vended by the box's /token)
  AUTH_SERVICE_URL + AUTH_SERVICE_TOKEN — fetch from the box's /token endpoint
"""

import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
import uuid

RH_BASE = "https://api.robinhood.com"
TRADING_DB_BASE = os.getenv(
    "TRADING_DB_BASE", "https://5thstreetcapital.netlify.app/.netlify/functions")

ACTIVE_STATES = {"queued", "confirmed", "unconfirmed", "partially_filled"}

_token_cache = {"token": None}
_symbol_cache: dict[str, str] = {}
_account_cache: dict[str, str] = {}


# --------------------------------------------------------------------------- #
# HTTP plumbing (module-level so tests can stub it)
# --------------------------------------------------------------------------- #

def http_json(method: str, url: str, body: dict | None = None,
              headers: dict | None = None, timeout: int = 30) -> dict:
    req = urllib.request.Request(
        url, method=method,
        data=json.dumps(body).encode() if body is not None else None,
        headers={"Content-Type": "application/json", "Accept": "application/json",
                 **(headers or {})})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.load(r)
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", "replace")[:500]
        raise RuntimeError(f"HTTP {e.code} from {url}: {detail}") from e


def rh_token() -> str:
    if _token_cache["token"]:
        return _token_cache["token"]
    token = os.getenv("RH_ACCESS_TOKEN")
    if not token:
        base, bearer = os.getenv("AUTH_SERVICE_URL"), os.getenv("AUTH_SERVICE_TOKEN")
        if not (base and bearer):
            raise RuntimeError(
                "no token: set RH_ACCESS_TOKEN, or AUTH_SERVICE_URL + AUTH_SERVICE_TOKEN")
        token = http_json("GET", f"{base.rstrip('/')}/token",
                          headers={"Authorization": f"Bearer {bearer}"})["token"]
    _token_cache["token"] = token
    return token


def rh_get(path_or_url: str) -> dict:
    url = path_or_url if path_or_url.startswith("http") else f"{RH_BASE}{path_or_url}"
    return http_json("GET", url, headers={"Authorization": f"Bearer {rh_token()}"})


def rh_post(path: str, body: dict) -> dict:
    return http_json("POST", f"{RH_BASE}{path}", body,
                     headers={"Authorization": f"Bearer {rh_token()}"})


def _paginate(url: str, max_items: int) -> list[dict]:
    out: list[dict] = []
    while url and len(out) < max_items:
        page = rh_get(url)
        out.extend(page.get("results", []))
        url = page.get("next")
    return out[:max_items]


def _symbol_for(instrument_url: str) -> str | None:
    if not instrument_url:
        return None
    if instrument_url not in _symbol_cache:
        try:
            _symbol_cache[instrument_url] = http_json("GET", instrument_url).get("symbol")
        except RuntimeError:
            return None
    return _symbol_cache[instrument_url]


def _account() -> dict:
    if not _account_cache:
        results = rh_get("/accounts/").get("results", [])
        if not results:
            raise RuntimeError("no accounts returned")
        _account_cache.update(results[0])
    return _account_cache


# --------------------------------------------------------------------------- #
# tools — reads + the one sanctioned write (trailing stop)
# --------------------------------------------------------------------------- #

def tool_get_stock_orders(limit: int = 100, state: str | None = None) -> dict:
    orders = _paginate(f"{RH_BASE}/orders/?page_size=100", int(limit))
    if state:
        orders = [o for o in orders if o.get("state") == state]
    for o in orders:
        o["symbol"] = _symbol_for(o.get("instrument", ""))
    return {"count": len(orders), "orders": orders}


def tool_get_option_orders(limit: int = 100, state: str | None = None) -> dict:
    orders = _paginate(f"{RH_BASE}/options/orders/?page_size=100", int(limit))
    if state:
        orders = [o for o in orders if o.get("state") == state]
    return {"count": len(orders), "orders": orders}


def tool_get_positions(nonzero: bool = True) -> dict:
    qs = "?nonzero=true" if nonzero else ""
    positions = _paginate(f"{RH_BASE}/positions/{qs}", 500)
    for p in positions:
        p["symbol"] = _symbol_for(p.get("instrument", ""))
    return {"count": len(positions), "positions": positions}


def tool_get_trailing_stop_orders() -> dict:
    orders = _paginate(f"{RH_BASE}/orders/?page_size=100", 1000)
    out = []
    for o in orders:
        if o.get("state") not in ACTIVE_STATES or o.get("trigger") != "stop":
            continue
        peg = o.get("trailing_peg") or {}
        if peg.get("type") not in (None, "percentage"):
            continue
        o["symbol"] = _symbol_for(o.get("instrument", ""))
        out.append(o)
    return {"count": len(out), "orders": out}


def _trailing_payload(symbol: str, side: str, quantity, trail_percent,
                      time_in_force: str = "gtc") -> dict:
    if side not in ("buy", "sell"):
        raise ValueError("side must be 'buy' or 'sell'")
    if not 0 < float(trail_percent) <= 100:
        raise ValueError("trail_percent must be in (0, 100]")
    if float(quantity) <= 0:
        raise ValueError("quantity must be > 0")
    found = http_json(
        "GET", f"{RH_BASE}/instruments/?symbol={urllib.parse.quote(symbol)}"
    ).get("results", [])
    if not found:
        raise ValueError(f"unknown symbol {symbol!r}")
    acct = _account()
    return {
        "account": acct.get("url", ""),
        "instrument": found[0]["url"],
        "symbol": symbol,
        "type": "market",
        "time_in_force": time_in_force,
        "trigger": "stop",
        "side": side,
        "quantity": str(quantity),
        "trailing_peg": {"type": "percentage", "percentage": str(trail_percent)},
        "ref_id": str(uuid.uuid4()),
    }


def tool_place_trailing_stop(symbol: str, side: str, quantity, trail_percent,
                             time_in_force: str = "gtc", dry_run: bool = True) -> dict:
    payload = _trailing_payload(symbol, side, quantity, trail_percent, time_in_force)
    if dry_run:
        return {"dry_run": True, "method": "POST", "url": f"{RH_BASE}/orders/",
                "payload": payload}
    return rh_post("/orders/", payload)


def tool_replace_trailing_stop(order_id: str, symbol: str, side: str, quantity,
                               trail_percent, time_in_force: str = "gtc",
                               dry_run: bool = True) -> dict:
    payload = _trailing_payload(symbol, side, quantity, trail_percent, time_in_force)
    url = f"/orders/{order_id}/replace/"
    if dry_run:
        return {"dry_run": True, "method": "POST", "url": f"{RH_BASE}{url}",
                "payload": payload}
    return rh_post(url, payload)


def tool_sync_trading_db(stock_limit: int = 700, option_limit: int = 300) -> dict:
    """Push RH order history into the 5thstreetcapital Trading DB (open writes)."""
    stock = tool_get_stock_orders(limit=stock_limit)["orders"]
    option = tool_get_option_orders(limit=option_limit)["orders"]
    upserted = {"stock": 0, "option": 0}
    for i in range(0, len(stock), 100):
        res = http_json("POST", f"{TRADING_DB_BASE}/db-orders",
                        {"orders": stock[i:i + 100]})
        upserted["stock"] += res["data"]["stock_upserted"]
    for i in range(0, len(option), 100):
        res = http_json("POST", f"{TRADING_DB_BASE}/db-orders",
                        {"option_orders": option[i:i + 100]})
        upserted["option"] += res["data"]["option_upserted"]
    return {"pulled": {"stock": len(stock), "option": len(option)},
            "upserted": upserted}


TOOLS = {
    "get_stock_orders": (tool_get_stock_orders, "Stock orders (newest first), symbols resolved.", {
        "limit": {"type": "integer", "default": 100},
        "state": {"type": "string", "description": "filter: filled/queued/cancelled/…"},
    }),
    "get_option_orders": (tool_get_option_orders, "Option orders (newest first).", {
        "limit": {"type": "integer", "default": 100},
        "state": {"type": "string"},
    }),
    "get_positions": (tool_get_positions, "Current stock positions.", {
        "nonzero": {"type": "boolean", "default": True},
    }),
    "get_trailing_stop_orders": (tool_get_trailing_stop_orders,
                                 "Active percentage trailing-stop orders.", {}),
    "place_trailing_stop": (tool_place_trailing_stop,
                            "Place a percentage trailing-stop order (the only sanctioned write). dry_run defaults true.", {
        "symbol": {"type": "string"}, "side": {"type": "string", "enum": ["buy", "sell"]},
        "quantity": {"type": "number"}, "trail_percent": {"type": "number"},
        "time_in_force": {"type": "string", "default": "gtc"},
        "dry_run": {"type": "boolean", "default": True},
    }),
    "replace_trailing_stop": (tool_replace_trailing_stop,
                              "Replace a trailing-stop order by id. dry_run defaults true.", {
        "order_id": {"type": "string"}, "symbol": {"type": "string"},
        "side": {"type": "string", "enum": ["buy", "sell"]},
        "quantity": {"type": "number"}, "trail_percent": {"type": "number"},
        "time_in_force": {"type": "string", "default": "gtc"},
        "dry_run": {"type": "boolean", "default": True},
    }),
    "sync_trading_db": (tool_sync_trading_db,
                        "Pull RH order history and upsert it into the 5thstreetcapital Trading DB.", {
        "stock_limit": {"type": "integer", "default": 700},
        "option_limit": {"type": "integer", "default": 300},
    }),
}


# --------------------------------------------------------------------------- #
# MCP protocol (stdio, newline-delimited JSON-RPC 2.0)
# --------------------------------------------------------------------------- #

def _tool_list() -> list[dict]:
    return [{
        "name": name,
        "description": desc,
        "inputSchema": {"type": "object", "properties": props},
    } for name, (_, desc, props) in TOOLS.items()]


def handle_message(msg: dict) -> dict | None:
    method, msg_id = msg.get("method"), msg.get("id")
    if method and method.startswith("notifications/"):
        return None

    def ok(result):
        return {"jsonrpc": "2.0", "id": msg_id, "result": result}

    def err(code, message):
        return {"jsonrpc": "2.0", "id": msg_id,
                "error": {"code": code, "message": message}}

    if method == "initialize":
        return ok({
            "protocolVersion": msg.get("params", {}).get("protocolVersion", "2024-11-05"),
            "capabilities": {"tools": {}},
            "serverInfo": {"name": "robinhood-local", "version": "1.0.0"},
        })
    if method == "ping":
        return ok({})
    if method == "tools/list":
        return ok({"tools": _tool_list()})
    if method == "tools/call":
        params = msg.get("params") or {}
        name, args = params.get("name"), params.get("arguments") or {}
        if name not in TOOLS:
            return err(-32602, f"unknown tool {name!r}")
        try:
            result = TOOLS[name][0](**args)
            return ok({"content": [{"type": "text", "text": json.dumps(result)}],
                       "isError": False})
        except (RuntimeError, ValueError, TypeError) as e:
            return ok({"content": [{"type": "text", "text": str(e)}], "isError": True})
    return err(-32601, f"method {method!r} not supported")


def main():
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except ValueError:
            continue
        resp = handle_message(msg)
        if resp is not None:
            sys.stdout.write(json.dumps(resp) + "\n")
            sys.stdout.flush()


if __name__ == "__main__":
    main()
