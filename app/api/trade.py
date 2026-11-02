"""Trading API — submit and cancel orders."""

import os
from datetime import date, datetime

from flask import Blueprint, jsonify, request, current_app
from app.api.robinhood_proxy import _require_token
from app.brokers import get_broker
from app.enums import OrderSide, OrderType

bp = Blueprint("trade", __name__)

# Every route here places or cancels live orders, so the whole blueprint sits
# behind the same Bearer token as the robinhood proxy (fails closed if unset).
bp.before_request(_require_token)


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
        market_hours: str (optional) "regular_hours"|"extended_hours"|"all_day_hours",
                          default "regular_hours"; all_day_hours = 24 Hour Market
                          (limit orders only, whole shares only)
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

    order_type = body.get("order_type", "market").lower()
    limit_price = body.get("limit_price")
    stop_price = body.get("stop_price")
    market_hours = body.get("market_hours", "regular_hours").lower()

    if order_type in (OrderType.LIMIT, OrderType.STOP_LIMIT) and limit_price is None:
        return jsonify({"error": "limit_price required for limit/stop_limit orders"}), 400
    if order_type in (OrderType.STOP, OrderType.STOP_LIMIT) and stop_price is None:
        return jsonify({"error": "stop_price required for stop/stop_limit orders"}), 400
    if market_hours not in ("regular_hours", "extended_hours", "all_day_hours"):
        return jsonify({"error": "market_hours must be regular_hours, extended_hours, or all_day_hours"}), 400
    if market_hours == "all_day_hours":
        if order_type != OrderType.LIMIT:
            return jsonify({"error": "all_day_hours requires a limit order"}), 400
        if quantity != int(quantity):
            return jsonify({"error": "all_day_hours requires whole shares"}), 400

    # --- check dry_run ---
    dry_run = body.get("dry_run", body.get("dryRun", current_app.config.get("DRY_RUN", True)))

    order_dict = {
        "symbol": symbol.upper(),
        "side": side,
        "quantity": quantity,
        "order_type": order_type,
        "limit_price": limit_price,
        "stop_price": stop_price,
        "market_hours": market_hours,
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
    data["side"] = OrderSide.BUY
    request._cached_json = (data, data)
    return place_order(broker_name)


@bp.route("/trade/sell", methods=["POST"])
@bp.route("/trade/sell/<broker_name>", methods=["POST"])
def sell(broker_name=None):
    """Convenience: place a SELL order."""
    data = request.get_json(force=True)
    data["side"] = OrderSide.SELL
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


# --- guarded vertical debit spreads ------------------------------------------

def _spread_limits() -> dict:
    """Hard caps for /trade/option-spread, overridable via env."""
    return {
        "tickers": {t.strip().upper() for t in
                    os.getenv("OPTION_SPREAD_TICKERS", "AMD").split(",") if t.strip()},
        "max_qty": int(os.getenv("OPTION_SPREAD_MAX_QTY", "2")),
        "max_risk": float(os.getenv("OPTION_SPREAD_MAX_RISK_USD", "3000")),
        "max_days": int(os.getenv("OPTION_SPREAD_MAX_DAYS", "10")),
    }


def validate_spread(body: dict, today: date | None = None) -> tuple[dict | None, list[str]]:
    """Validate a call/put vertical debit spread request against the hard caps.

    Args:
        body: Request JSON (symbol, expiration, option_type, long_strike,
            short_strike, quantity, limit_price).
        today: Override for the current date (tests).

    Returns:
        ``(spread, errors)``; ``spread`` is the normalized dict when errors is empty.
    """
    lim, errs = _spread_limits(), []
    symbol = str(body.get("symbol", "")).upper()
    if symbol not in lim["tickers"]:
        errs.append(f"symbol must be one of {sorted(lim['tickers'])}")
    otype = str(body.get("option_type", "call")).lower()
    if otype not in ("call", "put"):
        errs.append("option_type must be 'call' or 'put'")
    try:
        long_k, short_k = float(body["long_strike"]), float(body["short_strike"])
        qty, px = int(body["quantity"]), float(body["limit_price"])
    except (KeyError, TypeError, ValueError):
        return None, errs + ["long_strike, short_strike, quantity, limit_price are required numbers"]
    if not 1 <= qty <= lim["max_qty"]:
        errs.append(f"quantity must be 1..{lim['max_qty']}")
    width = abs(short_k - long_k)
    if width <= 0:
        errs.append("strikes must differ")
    elif (otype == "call") != (long_k < short_k):
        errs.append("debit spread needs long strike below short for calls, above for puts")
    if not 0 < px < width:
        errs.append("limit_price must be >0 and below the strike width")
    if px * 100 * qty > lim["max_risk"]:
        errs.append(f"total debit exceeds ${lim['max_risk']:.0f} cap")
    try:
        exp = datetime.strptime(str(body.get("expiration")), "%Y-%m-%d").date()
        days = (exp - (today or date.today())).days
        if not 0 <= days <= lim["max_days"]:
            errs.append(f"expiration must be within {lim['max_days']} days")
    except ValueError:
        errs.append("expiration must be YYYY-MM-DD")
    if errs:
        return None, errs
    return {"symbol": symbol, "expiration": body["expiration"], "option_type": otype,
            "long_strike": long_k, "short_strike": short_k, "quantity": qty,
            "limit_price": round(px, 2)}, []


@bp.route("/trade/option-spread", methods=["POST"])
@bp.route("/trade/option-spread/<broker_name>", methods=["POST"])
def place_option_spread(broker_name=None):
    """Place a capped vertical debit spread (limit, good-for-day). Dry-run by default.

    Live submission needs ``"dry_run": false`` in the body AND the global
    DRY_RUN config off, so a single flag can't send a real order.
    """
    broker_name = broker_name or current_app.config["DEFAULT_BROKER"]
    body = request.get_json(silent=True) or {}
    spread, errs = validate_spread(body)
    if errs:
        return jsonify({"error": "Validation failed", "details": errs}), 400
    dry_run = body.get("dry_run", True) is not False or current_app.config.get("DRY_RUN", True)
    if dry_run:
        return jsonify({"status": "simulated", "dry_run": True,
                        "broker": broker_name, "spread": spread,
                        "max_loss_usd": round(spread["limit_price"] * 100 * spread["quantity"], 2)})
    broker = get_broker(broker_name)
    if not hasattr(broker, "place_option_spread"):
        return jsonify({"error": f"{broker_name} does not support option spreads"}), 501
    result = broker.place_option_spread(spread)
    if result is None:
        return jsonify({"error": "Broker rejected the spread", "spread": spread}), 502
    return jsonify({"status": "submitted", "broker": broker_name,
                    "orderId": result["id"], "state": result.get("state"),
                    "spread": spread}), 201
