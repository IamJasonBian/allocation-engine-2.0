"""Order book snapshot API — serves the latest snapshot from Netlify Blobs."""

import logging
import os
from datetime import datetime, timezone

import requests as http_requests
from flask import Blueprint, jsonify

from app.brokers import get_broker

log = logging.getLogger(__name__)

bp = Blueprint("snapshot", __name__)

BLOBS_URL = "https://api.netlify.com/api/v1/blobs"
STORE_NAME = "order-book"


def _fetch_blob(key: str) -> dict | None:
    token = os.getenv("NETLIFY_API_TOKEN")
    site_id = os.getenv("NETLIFY_SITE_ID")
    if not token or not site_id:
        return None
    url = f"{BLOBS_URL}/{site_id}/{STORE_NAME}/{key}"
    try:
        resp = http_requests.get(url, headers={"Authorization": f"Bearer {token}"}, timeout=15)
        if resp.status_code == 404:
            return None
        resp.raise_for_status()
        return resp.json()
    except Exception:
        log.exception("Failed to fetch blob %s/%s", STORE_NAME, key)
        return None


@bp.route("/snapshot")
def snapshot():
    """Return the latest order book snapshot, building it live if no blob exists."""
    # Try blob store first
    data = _fetch_blob("latest")
    if data:
        # Transform to the OrderBookSnapshot format the frontend expects
        positions = data.get("positions", [])
        open_orders = data.get("open_orders", [])
        account = data.get("account", {})

        snapshot_positions = []
        for p in positions:
            qty = float(p.get("qty", 0))
            avg_buy = float(p.get("avg_entry", 0))
            current_price = float(p.get("current_price", avg_buy))
            equity = float(p.get("market_value", qty * current_price))
            pl = float(p.get("unrealized_pl", 0))
            pl_pct = float(p.get("unrealized_pl_pct", 0))

            snapshot_positions.append({
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
            })

        snapshot_orders = []
        for o in open_orders:
            snapshot_orders.append({
                "order_id": o.get("id", ""),
                "symbol": o.get("symbol", ""),
                "side": o.get("side", ""),
                "order_type": o.get("type", "market"),
                "trigger": "immediate",
                "state": o.get("status", o.get("state", "")),
                "quantity": float(o.get("qty", 0)),
                "limit_price": float(o["limit_price"]) if o.get("limit_price") else 0,
                "stop_price": float(o["stop_price"]) if o.get("stop_price") else None,
                "created_at": "",
                "updated_at": "",
            })

        # Include options from blob if available
        options_positions = data.get("options_positions", [])
        option_orders = data.get("option_orders", [])

        return jsonify({
            "timestamp": data.get("timestamp", datetime.now(timezone.utc).isoformat()),
            "order_book": snapshot_orders,
            "portfolio": {
                "cash": {
                    "cash": account.get("cash", 0),
                    "cash_available_for_withdrawal": account.get("cash", 0),
                    "buying_power": account.get("buying_power", 0),
                    "tradeable_cash": account.get("cash", 0),
                },
                "equity": account.get("equity", 0),
                "market_value": account.get("portfolio_value", 0),
                "positions": snapshot_positions,
                "open_orders": snapshot_orders,
                "open_option_orders": [o for o in option_orders
                                       if o.get("state") in ("queued", "confirmed",
                                                              "partially_filled", "pending")],
                "options": options_positions,
            },
            "market_data": None,
        })

    # No blob — build live from broker
    try:
        from flask import current_app
        broker_name = current_app.config.get("DEFAULT_BROKER", "robinhood")
        broker = get_broker(broker_name)
        account = broker.account()
        positions = broker.positions()
        open_orders = broker.open_orders()

        # Fetch options if broker supports them
        options_positions = []
        option_orders_raw = []
        if hasattr(broker, "options_positions"):
            try:
                options_positions = broker.options_positions()
            except Exception:
                log.exception("Failed to fetch live options positions")
        if hasattr(broker, "options_orders"):
            try:
                option_orders_raw = broker.options_orders(limit=50)
            except Exception:
                log.exception("Failed to fetch live options orders")

        snapshot_positions = []
        for p in positions:
            qty = float(p.get("qty", 0))
            avg_buy = float(p.get("avg_entry", 0))
            current_price = float(p.get("current_price", avg_buy))
            equity = float(p.get("market_value", qty * current_price))

            snapshot_positions.append({
                "symbol": p.get("symbol", ""),
                "name": p.get("symbol", ""),
                "quantity": qty,
                "avg_buy_price": avg_buy,
                "current_price": current_price,
                "equity": equity,
                "profit_loss": float(p.get("unrealized_pl", 0)),
                "profit_loss_pct": float(p.get("unrealized_pl_pct", 0)) * 100,
                "percent_change": None,
                "percentage": None,
            })

        snapshot_orders = [{
            "order_id": o.get("id", ""),
            "symbol": o.get("symbol", ""),
            "side": o.get("side", ""),
            "order_type": o.get("type", "market"),
            "trigger": "immediate",
            "state": o.get("status", ""),
            "quantity": float(o.get("qty", 0)),
            "limit_price": float(o["limit_price"]) if o.get("limit_price") else 0,
            "stop_price": float(o["stop_price"]) if o.get("stop_price") else None,
            "created_at": "",
            "updated_at": "",
        } for o in open_orders]

        open_option_states = {"queued", "confirmed", "partially_filled", "pending"}
        open_option_orders = [o for o in option_orders_raw
                              if o.get("state") in open_option_states]

        return jsonify({
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "order_book": snapshot_orders,
            "portfolio": {
                "cash": {
                    "cash": account.get("cash", 0),
                    "cash_available_for_withdrawal": account.get("cash", 0),
                    "buying_power": account.get("buying_power", 0),
                    "tradeable_cash": account.get("cash", 0),
                },
                "equity": account.get("equity", 0),
                "market_value": account.get("portfolio_value", 0),
                "positions": snapshot_positions,
                "open_orders": snapshot_orders,
                "open_option_orders": open_option_orders,
                "options": options_positions,
            },
            "market_data": None,
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500
