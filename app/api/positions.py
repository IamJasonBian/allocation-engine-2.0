from flask import Blueprint, jsonify, current_app
from app.brokers import get_broker
from app.schemas import validate_positions

bp = Blueprint("positions", __name__)


@bp.route("/positions")
@bp.route("/positions/<broker_name>")
def positions(broker_name=None):
    broker_name = broker_name or current_app.config["DEFAULT_BROKER"]
    try:
        broker = get_broker(broker_name)
        data = broker.positions()
        validated = validate_positions(data)
        dumped = [p.model_dump() for p in validated]
        return jsonify({"broker": broker_name, "count": len(dumped), "positions": dumped})
    except Exception as e:
        return jsonify({"error": str(e)}), 500
