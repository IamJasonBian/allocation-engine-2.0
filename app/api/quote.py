"""Quote API — fetch live prices from the data broker (Alpaca)."""

from flask import Blueprint, jsonify, request, current_app
from app.brokers import get_broker

bp = Blueprint("quote", __name__)


@bp.route("/quote/<symbol>")
def quote(symbol):
    """Get latest quote for a symbol via the data broker (Alpaca)."""
    data_broker_name = current_app.config.get("DATA_BROKER", "")
    if not data_broker_name:
        return jsonify({"error": "DATA_BROKER not configured"}), 500

    try:
        broker = get_broker(data_broker_name)
        if not hasattr(broker, "get_latest_quote"):
            # Fall back to get_latest_prices
            prices = broker.get_latest_prices([symbol.upper()])
            price = prices.get(symbol.upper())
            if price is None:
                return jsonify({"error": f"No quote found for {symbol}"}), 404
            return jsonify({
                "symbol": symbol.upper(),
                "price": price,
                "bidPrice": None,
                "askPrice": price,
                "previousClose": None,
            })

        data = broker.get_latest_quote(symbol.upper())
        return jsonify(data)
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@bp.route("/quotes")
def quotes():
    """Get latest quotes for multiple symbols (comma-separated query param)."""
    symbols_str = request.args.get("symbols", "")
    if not symbols_str:
        return jsonify({"error": "symbols query param required"}), 400

    symbols = [s.strip().upper() for s in symbols_str.split(",") if s.strip()]
    data_broker_name = current_app.config.get("DATA_BROKER", "")
    if not data_broker_name:
        return jsonify({"error": "DATA_BROKER not configured"}), 500

    try:
        broker = get_broker(data_broker_name)
        prices = broker.get_latest_prices(symbols)
        return jsonify({"prices": prices})
    except Exception as e:
        return jsonify({"error": str(e)}), 500
