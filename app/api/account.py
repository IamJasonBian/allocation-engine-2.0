from dataclasses import asdict

from flask import Blueprint, jsonify, current_app
from app.brokers import get_broker

bp = Blueprint("account", __name__)


@bp.route("/account")
@bp.route("/account/<broker_name>")
def account(broker_name=None):
    broker_name = broker_name or current_app.config["DEFAULT_BROKER"]
    try:
        broker = get_broker(broker_name)
        data = broker.account()
        return jsonify({"broker": broker_name, **asdict(data)})
    except Exception as e:
        return jsonify({"error": str(e)}), 500
