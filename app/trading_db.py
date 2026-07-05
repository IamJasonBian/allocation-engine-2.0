"""Write-path client for the 5thstreetcapital Trading DB (Netlify functions).

POST {TRADING_DB_URL}/db-orders        — idempotent upsert keyed on order_id;
                                         accepts the engine-blob dump shape
                                         (open_orders / recent_orders /
                                         open_option_orders / recent_option_orders)
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


def post_bot_activity(events):
    """Append bot activity events (de-dup key {order_id}:{status})."""
    if not events:
        return None
    return _post("/db-bot-activity", {"events": list(events)})
