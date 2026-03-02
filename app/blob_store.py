"""Netlify Blobs sync — uploads order book snapshots to the 'order-book' store.

Writes snapshots in the schema expected by the allocation-manager frontend
(OrderBookSnapshot type in robinhoodService.ts).
"""

import json
import logging
import os
from datetime import datetime, timezone

import requests

log = logging.getLogger(__name__)

BLOBS_URL = "https://api.netlify.com/api/v1/blobs"
STORE_NAME = "order-book"


def sync_to_blob(positions, open_orders, account):
    """Upload current portfolio state to Netlify Blobs.

    Writes two keys:
      - 'latest': always-current snapshot for the frontend
      - '{timestamp}': timestamped copy for history
    """
    token = os.getenv("NETLIFY_API_TOKEN")
    site_id = os.getenv("NETLIFY_SITE_ID")
    if not token or not site_id:
        log.debug("[blob] NETLIFY_API_TOKEN or NETLIFY_SITE_ID not set, skipping")
        return

    now = datetime.now(timezone.utc)
    ts = now.isoformat()

    # Map positions to the frontend SnapshotPosition schema
    snap_positions = [
        {
            "symbol": p.symbol,
            "quantity": p.qty,
            "avg_buy_price": p.avg_entry,
            "current_price": round(p.market_value / p.qty, 4) if p.qty else 0,
            "equity": p.market_value,
            "profit_loss": p.unrealized_pl,
            "profit_loss_pct": p.unrealized_pl_pct,
        }
        for p in positions
    ]

    # Map orders to the frontend SnapshotOrder schema
    snap_orders = [
        {
            "order_id": o.id,
            "symbol": o.symbol,
            "side": o.side.upper(),
            "order_type": o.order_type,
            "trigger": "immediate",
            "state": o.status,
            "quantity": o.qty,
            "limit_price": o.limit_price or 0,
            "stop_price": o.stop_price,
            "created_at": ts,
            "updated_at": ts,
        }
        for o in open_orders
    ]

    snapshot = {
        "timestamp": ts,
        "portfolio": {
            "cash": {
                "cash": account.cash,
                "buying_power": account.buying_power,
                "cash_available_for_withdrawal": account.cash,
                "tradeable_cash": account.cash,
            },
            "equity": account.equity,
            "market_value": account.portfolio_value,
            "positions": snap_positions,
            "open_orders": snap_orders,
        },
        "order_book": snap_orders,
        "recent_orders": [],
        "recent_option_orders": [],
    }

    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    }
    payload = json.dumps(snapshot)

    for blob_key in ("latest", now.strftime("%Y-%m-%dT%H-%M-%S")):
        url = f"{BLOBS_URL}/{site_id}/{STORE_NAME}/{blob_key}"
        try:
            resp = requests.put(url, headers=headers, data=payload, timeout=15)
            resp.raise_for_status()
            log.info("[blob] PUT %s/%s -> %s (%d bytes)",
                     STORE_NAME, blob_key, resp.status_code, len(payload))
        except Exception:
            log.exception("[blob] Failed to upload %s/%s", STORE_NAME, blob_key)
