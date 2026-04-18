from flask import Blueprint, jsonify, current_app
from app.brokers import get_broker
from app.schemas import validate_account

bp = Blueprint("account", __name__)


@bp.route("/account")
@bp.route("/account/<broker_name>")
def account(broker_name=None):
    broker_name = broker_name or current_app.config["DEFAULT_BROKER"]
    try:
        broker = get_broker(broker_name)
        data = broker.account()
        validated = validate_account(data)
        return jsonify({"broker": broker_name, **validated.model_dump()})
    except Exception as e:
        return jsonify({"error": str(e)}), 500
