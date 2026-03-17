"""Options API — positions and orders."""

from flask import Blueprint, jsonify, request, current_app
from app.brokers import get_broker

bp = Blueprint("options", __name__)


@bp.route("/options/positions")
@bp.route("/options/positions/<broker_name>")
def options_positions(broker_name=None):
    """Return current options positions."""
    broker_name = broker_name or current_app.config["DEFAULT_BROKER"]
    try:
        broker = get_broker(broker_name)
        if not hasattr(broker, "options_positions"):
            return jsonify({"error": f"Broker {broker_name} does not support options"}), 400
        data = broker.options_positions()
        return jsonify({"broker": broker_name, "count": len(data), "positions": data})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@bp.route("/options/orders")
@bp.route("/options/orders/<broker_name>")
def options_orders(broker_name=None):
    """Return recent options orders."""
    broker_name = broker_name or current_app.config["DEFAULT_BROKER"]
    limit = request.args.get("limit", 50, type=int)
    try:
        broker = get_broker(broker_name)
        if not hasattr(broker, "options_orders"):
            return jsonify({"error": f"Broker {broker_name} does not support options orders"}), 400
        data = broker.options_orders(limit=limit)
        return jsonify({"broker": broker_name, "count": len(data), "orders": data})
    except Exception as e:
        return jsonify({"error": str(e)}), 500
