"""Write-path client for the 5thstreetcapital Trading DB (Netlify functions).

POST {TRADING_DB_URL}/db-orders        — idempotent upsert keyed on order_id;
                                         accepts the engine-blob dump shape
                                         (open_orders / recent_orders /
                                         open_option_orders / recent_option_orders)
POST {TRADING_DB_URL}/db-positions     — whole-book replace of the position
                                         set (stock + option) plus the account
                                         summary
POST {TRADING_DB_URL}/db-bot-activity  — append-only events, de-duped on
                                         {order_id}:{status}

Writes are currently open; when TRADING_DB_TOKEN is set we send it as a
Bearer. Both calls log-and-return-None on failure — a frontend outage must
never break an engine tick.
"""

import logging

import requests

from app.config import Config

log = logging.getLogger(__name__)


def _headers():
    h = {"Content-Type": "application/json"}
    if Config.TRADING_DB_TOKEN:
        h["Authorization"] = f"Bearer {Config.TRADING_DB_TOKEN}"
    return h


def _get(path):
    url = f"{Config.TRADING_DB_URL.rstrip('/')}{path}"
    try:
        r = requests.get(url, headers=_headers(), timeout=20)
        data = r.json() if r.content else {}
        if not r.ok or data.get("ok") is False:
            log.warning("[trading-db] GET %s -> %s %s", path, r.status_code,
                        str(data)[:300])
            return None
        return data
    except Exception as e:  # noqa: BLE001
        log.warning("[trading-db] GET %s failed: %s", path, e)
        return None


def _post(path, body):
    url = f"{Config.TRADING_DB_URL.rstrip('/')}{path}"
    try:
        r = requests.post(url, json=body, headers=_headers(), timeout=20)
        data = r.json() if r.content else {}
        if not r.ok or data.get("ok") is False:
            log.warning("[trading-db] POST %s -> %s %s", path, r.status_code,
                        str(data)[:300])
            return None
        return data
    except Exception as e:  # noqa: BLE001
        log.warning("[trading-db] POST %s failed: %s", path, e)
        return None


def post_orders(open_orders=None, recent_orders=None,
                open_option_orders=None, recent_option_orders=None):
    """Upsert stock + option orders (engine-blob dump shape)."""
    body = {}
    if open_orders:
        body["open_orders"] = list(open_orders)
    if recent_orders:
        body["recent_orders"] = list(recent_orders)
    if open_option_orders:
        body["open_option_orders"] = [dict(o) for o in open_option_orders]
    if recent_option_orders:
        body["recent_option_orders"] = [dict(o) for o in recent_option_orders]
    if not body:
        return None
    return _post("/db-orders", body)


def post_positions(positions=None, option_positions=None, account=None):
    """Replace the stored position book with the engine's current view.

    This is a whole-book replace, not a merge: the Trading DB drops any symbol
    absent from the payload, so a closed position disappears instead of
    lingering as a stale row. Callers must pass the complete book.

    Args:
        positions: Stock positions from BrokerClient.positions().
        option_positions: Option positions from BrokerClient.options_positions().
        account: Account summary from BrokerClient.account().

    Returns:
        The parsed response envelope, or None when the call failed or there
        was nothing to send.
    """
    # An empty book is a meaningful payload — it clears every stale row — so
    # only a call with nothing at all is treated as "nothing to send".
    if positions is None and option_positions is None and account is None:
        return None
    body = {
        "positions": [dict(p) for p in (positions or [])],
        "option_positions": [dict(o) for o in (option_positions or [])],
    }
    if account:
        body["account"] = dict(account)
    return _post("/db-positions", body)


def get_orders():
    """List stock/option orders from the Trading DB."""
    return _get("/db-orders")


def get_positions():
    """List the current position book from the Trading DB."""
    return _get("/db-positions")


def fills_from_orders(payload: dict) -> list[dict]:
    """Normalize db-orders rows into pnl fill dicts."""
    data = (payload or {}).get("data") or {}
    rows = []
    for key in ("historical_orders", "untracked_orders", "open_orders"):
        rows.extend(data.get(key) or [])
    fills = []
    for o in rows:
        qty = o.get("filled_quantity")
        px = o.get("average_price")
        if not qty or px in (None, ""):
            continue
        ts = o.get("updated_at") or o.get("created_at")
        if not ts:
            continue
        fills.append({
            "symbol": o["symbol"],
            "side": o["side"],
            "qty": float(qty),
            "price": float(px),
            "ts": ts,
        })
    return fills


def today_walk_from_db(day=None) -> dict:
    """Live book + today's fills from Trading DB GETs (not the box)."""
    from datetime import date as date_cls, datetime, timezone
    from app.pnl import today_walk
    if day is None:
        day = datetime.now(timezone.utc).date()
    elif isinstance(day, str):
        day = date_cls.fromisoformat(day[:10])
    pos = get_positions() or {}
    orders = get_orders() or {}
    book = ((pos.get("data") or {}).get("positions") or [])
    return {
        "asOf": (pos.get("data") or {}).get("updated_at") or pos.get("as_of"),
        "day": day.isoformat(),
        "account": (pos.get("data") or {}).get("account"),
        "tickers": today_walk(book, fills_from_orders(orders), day),
    }


def post_bot_activity(events):
    """Append bot activity events (de-dup key {order_id}:{status})."""
    if not events:
        return None
    return _post("/db-bot-activity", {"events": list(events)})
