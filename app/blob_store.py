"""Netlify Blobs sync — uploads order book snapshots to the 'order-book' store."""

import json
import logging
import os
from datetime import datetime, timezone

import requests

log = logging.getLogger(__name__)

BLOBS_URL = "https://api.netlify.com/api/v1/blobs"
STORE_NAME = "order-book"


def _build_frontend_snapshot(positions, open_orders, account,
                            options_positions, option_orders, now):
    """Build a snapshot in the shape the frontend's order-book-snapshot expects.

    The frontend reads from the 'state-logs' store and expects:
      - portfolio.positions, portfolio.equity, portfolio.cash, etc.
      - order_book (open equity orders)
      - recent_orders / recent_option_orders (filled orders for P&L)
      - portfolio.open_option_orders, portfolio.options
    """
    open_states = {"queued", "confirmed", "partially_filled", "pending",
                   "unconfirmed"}

    # Separate open vs filled option orders
    open_opt = [dict(o) for o in option_orders
                if o.get("state") in open_states]
    recent_opt = [dict(o) for o in option_orders
                  if o.get("state") not in open_states]

    # Build order_book (open equity orders) in the shape the frontend expects
    order_book = []
    for o in open_orders:
        order_book.append({
            "order_id": o.get("id", ""),
            "symbol": o.get("symbol", ""),
            "side": (o.get("side", "") or "").upper(),
            "order_type": o.get("type", "market"),
            "trigger": o.get("trigger", "immediate"),
            "state": o.get("status", o.get("state", "")),
            "quantity": float(o.get("qty", 0) or 0),
            "limit_price": float(o["limit_price"]) if o.get("limit_price") else None,
            "stop_price": float(o["stop_price"]) if o.get("stop_price") else None,
            "created_at": o.get("created_at", ""),
            "updated_at": o.get("updated_at", ""),
        })

    # Build positions in the shape the frontend expects
    snap_positions = []
    for p in positions:
        qty = float(p.get("qty", 0) or 0)
        avg_buy = float(p.get("avg_entry", 0) or 0)
        current_price = float(p.get("current_price", avg_buy) or avg_buy)
        equity = float(p.get("market_value", qty * current_price) or 0)
        pl = float(p.get("unrealized_pl", 0) or 0)
        pl_pct = float(p.get("unrealized_pl_pct", 0) or 0)

        snap_positions.append({
            "symbol": p.get("symbol", ""),
            "name": p.get("symbol", ""),
            "quantity": qty,
            "avg_buy_price": avg_buy,
            "current_price": current_price,
            "equity": equity,
            "profit_loss": pl,
            "profit_loss_pct": pl_pct * 100 if abs(pl_pct) < 1 else pl_pct,
            "percent_change": None,
            "percentage": None,
            "asset_type": p.get("asset_type", "equity"),
        })

    return {
        "timestamp": now.isoformat(),
        "portfolio": {
            "cash": {
                "cash": account.get("cash", 0),
                "cash_available_for_withdrawal": account.get("cash", 0),
                "buying_power": account.get("buying_power", 0),
                "tradeable_cash": account.get("cash", 0),
            },
            "equity": account.get("equity", 0),
            "market_value": account.get("portfolio_value", 0),
            "positions": snap_positions,
            "open_orders": order_book,
            "open_option_orders": open_opt,
            "options": options_positions,
        },
        "order_book": order_book,
        "recent_orders": [],
        "recent_option_orders": recent_opt,
        "market_data": None,
    }


def sync_to_blob(positions, open_orders, account,
                 options_positions=None, option_orders=None):
    """Upload current portfolio state to Netlify Blobs.

    Writes to two stores:
      - 'order-book': raw engine data (positions, orders, account)
      - 'state-logs': frontend-ready snapshot matching order-book-snapshot shape
    Each store gets a 'latest' key and a timestamped copy.
    """
    token = os.getenv("NETLIFY_API_TOKEN")
    site_id = os.getenv("NETLIFY_SITE_ID")
    if not token or not site_id:
        log.debug("[blob] NETLIFY_API_TOKEN or NETLIFY_SITE_ID not set, skipping")
        return

    if options_positions is None:
        options_positions = []
    if option_orders is None:
        option_orders = []

    now = datetime.now(timezone.utc)
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    }

    # --- Raw engine snapshot (order-book store) ---
    raw_snapshot = {
        "timestamp": now.isoformat(),
        "account": account,
        "positions": positions,
        "open_orders": open_orders,
        "options_positions": options_positions,
        "option_orders": [dict(o) for o in option_orders],
        "num_positions": len(positions),
        "num_open_orders": len(open_orders),
        "num_options_positions": len(options_positions),
        "num_option_orders": len(option_orders),
    }
    raw_payload = json.dumps(raw_snapshot)

    ts_key = now.strftime("%Y-%m-%dT%H-%M-%S")
    for blob_key in ("latest", ts_key):
        url = f"{BLOBS_URL}/{site_id}/{STORE_NAME}/{blob_key}"
        try:
            resp = requests.put(url, headers=headers, data=raw_payload, timeout=15)
            resp.raise_for_status()
            log.info("[blob] PUT %s/%s -> %s (%d bytes)",
                     STORE_NAME, blob_key, resp.status_code, len(raw_payload))
        except Exception:
            log.exception("[blob] Failed to upload %s/%s", STORE_NAME, blob_key)

    # --- Frontend-ready snapshot (state-logs store) ---
    frontend_snapshot = _build_frontend_snapshot(
        positions, open_orders, account,
        options_positions, option_orders, now,
    )
    frontend_payload = json.dumps(frontend_snapshot)

    for blob_key in ("latest", ts_key):
        url = f"{BLOBS_URL}/{site_id}/state-logs/{blob_key}"
        try:
            resp = requests.put(url, headers=headers, data=frontend_payload, timeout=15)
            resp.raise_for_status()
            log.info("[blob] PUT state-logs/%s -> %s (%d bytes)",
                     blob_key, resp.status_code, len(frontend_payload))
        except Exception:
            log.exception("[blob] Failed to upload state-logs/%s", blob_key)
