from dataclasses import asdict

from flask import Blueprint, jsonify, current_app
from app.brokers import get_broker

bp = Blueprint("positions", __name__)


@bp.route("/positions")
@bp.route("/positions/<broker_name>")
def positions(broker_name=None):
    broker_name = broker_name or current_app.config["DEFAULT_BROKER"]
    try:
        broker = get_broker(broker_name)
        data = broker.positions()
        return jsonify({"broker": broker_name, "count": len(data), "positions": [asdict(p) for p in data]})
    except Exception as e:
        return jsonify({"error": str(e)}), 500
