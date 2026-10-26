"""Options API — positions and orders."""

from flask import Blueprint, jsonify, request, current_app
from app.brokers import get_broker
from app.enums import OrderSide

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


@bp.route("/options/order", methods=["POST"])
@bp.route("/options/order/<broker_name>", methods=["POST"])
def submit_option_order(broker_name=None):
    """Place an option order.

    Body JSON:
        chain_symbol: str   (required) e.g. "AAPL"
        option_type:  str   (required) "call" or "put"
        strike:       float (required)
        expiration:   str   (required) e.g. "2026-06-19"
        side:         str   (required) "BUY" or "SELL"
        quantity:     float (required)
        limit_price:  float (optional)
        order_type:   str   (optional) default "limit"
    """
    broker_name = broker_name or current_app.config["DEFAULT_BROKER"]
    body = request.get_json(silent=True)
    if not body:
        return jsonify({"error": "JSON body required"}), 400

    # --- validate required fields ---
    chain_symbol = body.get("chain_symbol")
    option_type = body.get("option_type")
    strike = body.get("strike")
    expiration = body.get("expiration")
    side = body.get("side")
    quantity = body.get("quantity")

    errors = []
    if not chain_symbol or not isinstance(chain_symbol, str):
        errors.append("chain_symbol is required (string)")
    if option_type not in ("call", "put"):
        errors.append("option_type must be 'call' or 'put'")
    if strike is None:
        errors.append("strike is required")
    else:
        try:
            strike = float(strike)
            if strike <= 0:
                errors.append("strike must be positive")
        except (TypeError, ValueError):
            errors.append("strike must be a number")
    if not expiration or not isinstance(expiration, str):
        errors.append("expiration is required (string)")
    if side not in (OrderSide.BUY, OrderSide.SELL):
        errors.append("side must be 'BUY' or 'SELL'")
    if quantity is None:
        errors.append("quantity is required")
    else:
        try:
            quantity = float(quantity)
            if quantity <= 0:
                errors.append("quantity must be positive")
        except (TypeError, ValueError):
            errors.append("quantity must be a number")

    if errors:
        return jsonify({"error": "Validation failed", "details": errors}), 400

    order = {
        "chain_symbol": chain_symbol.upper(),
        "option_type": option_type,
        "strike": strike,
        "expiration": expiration,
        "side": side,
        "quantity": quantity,
        "limit_price": body.get("limit_price"),
        "order_type": body.get("order_type", "limit"),
    }

    try:
        broker = get_broker(broker_name)
        if not hasattr(broker, "submit_option_order"):
            return jsonify({"error": f"Broker {broker_name} does not support option orders"}), 400
        result = broker.submit_option_order(order)
        if result is None:
            return jsonify({"error": "Order submission failed"}), 502
        return jsonify({"broker": broker_name, "order": result})
    except Exception as e:
        return jsonify({"error": str(e)}), 500
