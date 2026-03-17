"""Trading API — submit and cancel orders."""

from flask import Blueprint, jsonify, request, current_app
from app.brokers import get_broker

bp = Blueprint("trade", __name__)


@bp.route("/trade/order", methods=["POST"])
@bp.route("/trade/order/<broker_name>", methods=["POST"])
def place_order(broker_name=None):
    """Place a buy or sell order directly.

    Body JSON:
        symbol:     str   (required) e.g. "AAPL"
        side:       str   (required) "BUY" or "SELL"
        quantity:   float (required)
        order_type: str   (optional) "market"|"limit"|"stop"|"stop_limit", default "market"
        limit_price: float (optional) required for limit/stop_limit
        stop_price:  float (optional) required for stop/stop_limit
        dry_run:     bool  (optional) override global dry_run setting
    """
    broker_name = broker_name or current_app.config["DEFAULT_BROKER"]
    body = request.get_json(silent=True)
    if not body:
        return jsonify({"error": "JSON body required"}), 400

    # --- validate required fields ---
    symbol = body.get("symbol")
    side = body.get("side")
    quantity = body.get("quantity")

    errors = []
    if not symbol or not isinstance(symbol, str):
        errors.append("symbol is required (string)")
    if side not in ("BUY", "SELL"):
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

    order_type = body.get("order_type", "market").lower()
    limit_price = body.get("limit_price")
    stop_price = body.get("stop_price")

    if order_type in ("limit", "stop_limit") and limit_price is None:
        return jsonify({"error": "limit_price required for limit/stop_limit orders"}), 400
    if order_type in ("stop", "stop_limit") and stop_price is None:
        return jsonify({"error": "stop_price required for stop/stop_limit orders"}), 400

    # --- check dry_run ---
    dry_run = body.get("dry_run", body.get("dryRun", current_app.config.get("DRY_RUN", True)))

    order_dict = {
        "symbol": symbol.upper(),
        "side": side,
        "quantity": quantity,
        "order_type": order_type,
        "limit_price": limit_price,
        "stop_price": stop_price,
    }

    if dry_run:
        return jsonify({
            "status": "simulated",
            "dry_run": True,
            "broker": broker_name,
            "order": order_dict,
            "message": "Order validated but not submitted (dry_run=true)",
        })

    try:
        broker = get_broker(broker_name)
        result = broker.submit_order(order_dict)
        if result is None:
            return jsonify({"error": "Broker rejected the order", "order": order_dict}), 502
        return jsonify({
            "status": "submitted",
            "broker": broker_name,
            "orderId": result.get("id"),
            "order": order_dict,
            "result": result,
        }), 201
    except Exception as e:
        return jsonify({"error": str(e), "order": order_dict}), 500


# Convenience aliases for the UI
@bp.route("/trade/buy", methods=["POST"])
@bp.route("/trade/buy/<broker_name>", methods=["POST"])
def buy(broker_name=None):
    """Convenience: place a BUY order."""
    data = request.get_json(force=True)
    data["side"] = "BUY"
    request._cached_json = (data, data)
    return place_order(broker_name)


@bp.route("/trade/sell", methods=["POST"])
@bp.route("/trade/sell/<broker_name>", methods=["POST"])
def sell(broker_name=None):
    """Convenience: place a SELL order."""
    data = request.get_json(force=True)
    data["side"] = "SELL"
    request._cached_json = (data, data)
    return place_order(broker_name)


@bp.route("/trade/cancel", methods=["POST", "DELETE"])
@bp.route("/trade/cancel/<order_id>", methods=["POST", "DELETE"])
@bp.route("/trade/cancel/<order_id>/<broker_name>", methods=["POST", "DELETE"])
def cancel_order(order_id=None, broker_name=None):
    """Cancel a specific order by ID."""
    broker_name = broker_name or current_app.config["DEFAULT_BROKER"]
    if order_id is None:
        body = request.get_json(silent=True) or {}
        order_id = body.get("order_id")
    if not order_id:
        return jsonify({"error": "order_id required"}), 400
    try:
        broker = get_broker(broker_name)
        broker.cancel_order(order_id)
        return jsonify({"status": "cancelled", "broker": broker_name, "order_id": order_id})
    except Exception as e:
        return jsonify({"error": str(e), "order_id": order_id}), 500


@bp.route("/trade/cancel-all", methods=["POST", "DELETE"])
@bp.route("/trade/cancel-all/<broker_name>", methods=["POST", "DELETE"])
def cancel_all(broker_name=None):
    """Cancel all open orders."""
    broker_name = broker_name or current_app.config["DEFAULT_BROKER"]
    try:
        broker = get_broker(broker_name)
        broker.cancel_all()
        return jsonify({"status": "all_cancelled", "broker": broker_name})
    except Exception as e:
        return jsonify({"error": str(e)}), 500
