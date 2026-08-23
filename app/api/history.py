"""Order history & P&L API."""

from flask import Blueprint, jsonify, request, current_app
from app.brokers import get_broker
from app.pnl import cost_basis_series, format_ticker_risk, pnl_risk, portfolio_risk
from app.slack import notify as telegram_notify

bp = Blueprint("history", __name__)


@bp.route("/orders/history")
@bp.route("/orders/history/<broker_name>")
def order_history(broker_name=None):
    """Return recent order history (all states, not just open)."""
    broker_name = broker_name or current_app.config["DEFAULT_BROKER"]
    limit = request.args.get("limit", 50, type=int)
    try:
        broker = get_broker(broker_name)
        if not hasattr(broker, "order_history"):
            return jsonify({"error": f"Broker {broker_name} does not support order history"}), 400
        data = broker.order_history(limit=limit)
        return jsonify({"broker": broker_name, "count": len(data), "orders": data})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@bp.route("/pnl")
@bp.route("/pnl/<broker_name>")
def pnl(broker_name=None):
    """Return P&L for the given time period (realized or total with marks)."""
    broker_name = broker_name or current_app.config["DEFAULT_BROKER"]
    days = request.args.get("days", 30, type=int)
    scope = request.args.get("scope", "realized")
    try:
        broker = get_broker(broker_name)
        if scope == "total":
            if not hasattr(broker, "total_pnl"):
                return jsonify({"error": f"Broker {broker_name} does not support total P&L"}), 400
            data = broker.total_pnl(days=days)
        else:
            if not hasattr(broker, "realized_pnl"):
                return jsonify({"error": f"Broker {broker_name} does not support P&L"}), 400
            data = broker.realized_pnl(days=days)
        return jsonify({"broker": broker_name, **data})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@bp.route("/pnl/risk")
def pnl_portfolio_risk():
    """Additive portfolio risk: sum of per-ticker |position| x stdev(closes) (?broker= to select)."""
    broker_name = request.args.get("broker") or current_app.config["DEFAULT_BROKER"]
    try:
        broker = get_broker(broker_name)
        if not hasattr(broker, "trade_fills") or not hasattr(broker, "price_history"):
            return jsonify({"error": f"Broker {broker_name} does not support risk series"}), 400
        fills = broker.trade_fills()
        symbols = sorted({f["symbol"] for f in fills})
        closes_by_symbol = {sym: broker.price_history(sym) for sym in symbols}
        data = portfolio_risk(fills, closes_by_symbol)
        return jsonify({"broker": broker_name, **data})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@bp.route("/pnl/risk/<symbol>")
@bp.route("/pnl/risk/<symbol>/<broker_name>")
def pnl_risk_series(symbol, broker_name=None):
    """Per-ticker close/growth-rate volatility and position-scaled USD risk."""
    broker_name = broker_name or current_app.config["DEFAULT_BROKER"]
    try:
        broker = get_broker(broker_name)
        if not hasattr(broker, "trade_fills") or not hasattr(broker, "price_history"):
            return jsonify({"error": f"Broker {broker_name} does not support risk series"}), 400
        data = pnl_risk(broker.trade_fills(), broker.price_history(symbol), symbol)
        text = format_ticker_risk(symbol.upper(), data)
        if request.args.get("notify"):
            telegram_notify(text)
        return jsonify({
            "broker": broker_name,
            "symbol": symbol.upper(),
            "text": text,
            **data,
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@bp.route("/pnl/basis/<symbol>")
@bp.route("/pnl/basis/<symbol>/<broker_name>")
def pnl_basis(symbol, broker_name=None):
    """Return historical average-cost basis after each fill for a symbol."""
    broker_name = broker_name or current_app.config["DEFAULT_BROKER"]
    try:
        broker = get_broker(broker_name)
        if not hasattr(broker, "trade_fills"):
            return jsonify({"error": f"Broker {broker_name} does not support fill history"}), 400
        points = cost_basis_series(broker.trade_fills(), symbol)
        return jsonify({
            "broker": broker_name,
            "symbol": symbol.upper(),
            "method": "avg_cost_full_history",
            "count": len(points),
            "points": points,
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500
