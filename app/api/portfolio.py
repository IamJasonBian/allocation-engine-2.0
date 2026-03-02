from dataclasses import asdict

from flask import Blueprint, jsonify, current_app
from app.brokers import get_broker

bp = Blueprint("portfolio", __name__)


@bp.route("/portfolio")
@bp.route("/portfolio/<broker_name>")
def portfolio(broker_name=None):
    broker_name = broker_name or current_app.config["DEFAULT_BROKER"]
    try:
        broker = get_broker(broker_name)
        acct = broker.account()
        pos = broker.positions()
        return jsonify({
            "broker": broker_name,
            "equity": acct.equity,
            "cash": acct.cash,
            "buying_power": acct.buying_power,
            "portfolio_value": acct.portfolio_value,
            "positions": [asdict(p) for p in pos],
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500
