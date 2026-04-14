"""Netlify Blobs sync for option position/order history.

Writes timestamp-keyed snapshots to two stores:
  - option-positions-history/{ISO-timestamp}
  - option-orders-history/{ISO-timestamp}

Unlike `blob_store.py` (which writes a single `latest` + timestamped copy in the
equity-first `order-book` store), this module is options-only and history-only:
every tick's option state is written to its own keyed blob so the series is
reconstructable later. Listing is done via the existing `vend-blobs` Netlify
function, matching the `options-chain` / `market-quotes` pattern.

Order snapshots are deduplicated against the previous tick's hash to avoid
thousands of identical blobs per day — a new blob is only written when the
observed (order_id, state) set changes.
"""

import hashlib
import json
import logging
import os
from datetime import datetime

import requests

log = logging.getLogger(__name__)

BLOBS_URL = "https://api.netlify.com/api/v1/blobs"
POSITIONS_STORE = "option-positions-history"
ORDERS_STORE = "option-orders-history"

# Module-level dedup state for orders. Reset on process restart — acceptable,
# worst case is one duplicate blob per restart.
_last_orders_hash: str | None = None


def _creds() -> tuple[str, str] | None:
    token = os.getenv("NETLIFY_API_TOKEN")
    site_id = os.getenv("NETLIFY_SITE_ID")
    if not token or not site_id:
        log.debug("[opt-history] NETLIFY_API_TOKEN or NETLIFY_SITE_ID not set, skipping")
        return None
    return token, site_id


def _put(store: str, key: str, payload: dict, token: str, site_id: str) -> None:
    url = f"{BLOBS_URL}/{site_id}/{store}/{key}"
    body = json.dumps(payload, default=str)
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    }
    try:
        resp = requests.put(url, headers=headers, data=body, timeout=15)
        resp.raise_for_status()
        log.info("[opt-history] PUT %s/%s -> %s (%d bytes)",
                 store, key, resp.status_code, len(body))
    except Exception:
        log.exception("[opt-history] Failed to upload %s/%s", store, key)


def put_position_snapshot(positions: list[dict], ts: datetime,
                          account: dict | None = None) -> None:
    """Write an option-positions snapshot blob.

    Always writes (even when `positions` is empty) so the series carries flat
    periods explicitly. Stamps `mark_stale: true` on any row where
    `mark_price == avg_price`, flagging the Robinhood stale-mark case for
    downstream filtering.
    """
    creds = _creds()
    if not creds:
        return
    token, site_id = creds

    positions = positions or []
    stamped: list[dict] = []
    for p in positions:
        row = dict(p)
        mark = row.get("mark_price")
        avg = row.get("avg_price")
        row["mark_stale"] = (mark is not None and avg is not None and mark == avg)
        stamped.append(row)

    underlying_prices: dict[str, float] = {}
    for p in stamped:
        sym = p.get("chain_symbol")
        up = p.get("underlying_price")
        if sym and up is not None:
            underlying_prices[sym] = up

    snapshot = {
        "timestamp": ts.isoformat(),
        "broker": os.getenv("ENGINE_BROKER", ""),
        "count": len(stamped),
        "positions": stamped,
        "underlying_prices": underlying_prices,
        "account_equity": (account or {}).get("equity"),
    }
    key = ts.strftime("%Y-%m-%dT%H-%M-%S")
    _put(POSITIONS_STORE, key, snapshot, token, site_id)


def _order_state_hash(orders: list[dict]) -> str:
    """Stable hash of the observable (order_id, state) set — dedup key."""
    pairs = sorted(
        (o.get("id") or o.get("order_id") or "", o.get("state") or "")
        for o in (orders or [])
    )
    h = hashlib.sha256()
    for oid, state in pairs:
        h.update(f"{oid}|{state}\n".encode())
    return h.hexdigest()


def put_order_snapshot(orders: list[dict], ts: datetime) -> None:
    """Write an option-orders snapshot blob if the order set has changed.

    Dedup is by hash of (order_id, state) across the list. Identical ticks are
    skipped so the store doesn't fill with duplicates during quiet periods.
    """
    global _last_orders_hash
    creds = _creds()
    if not creds:
        return
    token, site_id = creds

    orders = [dict(o) for o in (orders or [])]
    digest = _order_state_hash(orders)
    if digest == _last_orders_hash:
        log.debug("[opt-history] orders unchanged (hash=%s), skipping", digest[:12])
        return

    states_seen = sorted({o.get("state") for o in orders if o.get("state")})
    snapshot = {
        "timestamp": ts.isoformat(),
        "broker": os.getenv("ENGINE_BROKER", ""),
        "count": len(orders),
        "orders": orders,
        "states_seen": states_seen,
        "state_hash": digest,
    }
    key = ts.strftime("%Y-%m-%dT%H-%M-%S")
    _put(ORDERS_STORE, key, snapshot, token, site_id)
    _last_orders_hash = digest
