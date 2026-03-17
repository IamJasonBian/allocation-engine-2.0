"""Order history & P&L API."""

from flask import Blueprint, jsonify, request, current_app
from app.brokers import get_broker

bp = Blueprint("history", __name__)


@bp.route("/orders/history")
@bp.route("/orders/history/<broker_name>")
def order_history(broker_name=None):
    """Return recent order history (all states, not just open)."""
    broker_name = broker_name or current_app.config["DEFAULT_BROKER"]
    limit = request.args.get("limit", 50, type=int)
    try:
        broker = get_broker(broker_name)
        if not hasattr(broker, "order_history"):
            return jsonify({"error": f"Broker {broker_name} does not support order history"}), 400
        data = broker.order_history(limit=limit)
        return jsonify({"broker": broker_name, "count": len(data), "orders": data})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@bp.route("/pnl")
@bp.route("/pnl/<broker_name>")
def pnl(broker_name=None):
    """Return realized P&L for the given time period."""
    broker_name = broker_name or current_app.config["DEFAULT_BROKER"]
    days = request.args.get("days", 30, type=int)
    try:
        broker = get_broker(broker_name)
        if not hasattr(broker, "realized_pnl"):
            return jsonify({"error": f"Broker {broker_name} does not support P&L"}), 400
        data = broker.realized_pnl(days=days)
        return jsonify({"broker": broker_name, **data})
    except Exception as e:
        return jsonify({"error": str(e)}), 500
