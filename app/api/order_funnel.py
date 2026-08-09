"""Order-funnel view — placed → filled counts plus per-fill slippage.

Serves the "intent to fill" chart from IBKR order/execution history:
stage counts from this session's orders, slippage (avg_fill − limit, with
favorability by side) for filled limit orders, and today's raw executions.
The engine's pre-submission intents aren't queryable here yet, so the funnel
starts at "placed"; the response shape leaves room for an intents stage.

GET /api/viz/order-funnel          → ibkr
GET /api/viz/order-funnel/<broker> → 400 unless the broker keeps history
"""

from flask import Blueprint, jsonify

from app.brokers import get_broker

bp = Blueprint("viz_order_funnel", __name__)


@bp.route("/viz/order-funnel")
@bp.route("/viz/order-funnel/<broker_name>")
def order_funnel(broker_name="ibkr"):
    try:
        broker = get_broker(broker_name)
        if not hasattr(broker, "session_trades"):
            return jsonify({
                "error": f"order-funnel needs trade/execution history; "
                         f"broker '{broker_name}' does not expose it"
            }), 400

        trades = broker.session_trades()
        executions = broker.executions()

        fills = []
        for t in trades:
            if t["filled_qty"] <= 0 or t["limit_price"] is None or t["avg_fill_price"] is None:
                continue
            slippage = t["avg_fill_price"] - t["limit_price"]
            fills.append({
                "symbol": t["symbol"],
                "sec_type": t["sec_type"],
                "side": t["side"],
                "qty": t["filled_qty"],
                "limit_price": t["limit_price"],
                "avg_fill_price": t["avg_fill_price"],
                "slippage": slippage,
                "favorable": slippage >= 0 if t["side"] == "sell" else slippage <= 0,
            })

        return jsonify({
            "broker": broker_name,
            "scope": {"orders": "session", "executions": "today"},
            "stages": {
                "placed": len(trades),
                "filled": sum(1 for t in trades if t["status"] == "Filled"),
            },
            "fills": fills,
            "executions": executions,
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500
