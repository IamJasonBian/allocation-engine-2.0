from flask import Blueprint, jsonify, current_app
from app.brokers import get_broker
from app.schemas import validate_orders

bp = Blueprint("orders", __name__)


@bp.route("/orders")
@bp.route("/orders/<broker_name>")
def orders(broker_name=None):
    broker_name = broker_name or current_app.config["DEFAULT_BROKER"]
    try:
        broker = get_broker(broker_name)
        data = broker.open_orders()
        validated = validate_orders(data)
        dumped = [o.model_dump() for o in validated]
        return jsonify({"broker": broker_name, "count": len(dumped), "orders": dumped})
    except Exception as e:
        return jsonify({"error": str(e)}), 500
