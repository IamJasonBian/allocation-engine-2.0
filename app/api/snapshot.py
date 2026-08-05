"""Order book snapshot API — builds a live snapshot straight from the broker.

Reads go to Robinhood through the auth-service box (see
``app.brokers.robinhood_client``). This endpoint used to prefer a cached
Netlify Blobs "engine snapshot"; that store is no longer written — positions
now live in the Trading DB (``app.trading_db.post_positions``) and this route
serves the live book.
"""

import logging
from datetime import datetime, timezone

from flask import Blueprint, jsonify

from app.brokers import get_broker

log = logging.getLogger(__name__)

bp = Blueprint("snapshot", __name__)


@bp.route("/snapshot")
def snapshot():
    """Return a live order book snapshot built from the broker."""
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
                "asset_type": p.get("asset_type", "equity"),
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
