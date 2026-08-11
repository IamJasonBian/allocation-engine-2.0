"""Drift-vs-bands view — one evaluation of position drift against the rail.

Serves the "how close to acting?" chart: per-symbol drift (market vs entry,
the same definition _check_drift judges) plus the threshold it is judged
against. One call = one evaluation point; the dashboard accumulates or polls.

GET /api/viz/drift          → evaluated against the default ibkr broker
GET /api/viz/drift/<broker> → any registered broker (drift is broker-neutral)
"""

import logging
from datetime import datetime, timezone

from flask import Blueprint, jsonify

from app.brokers import get_broker
from app.engine import DRIFT_THRESHOLD

log = logging.getLogger(__name__)
bp = Blueprint("viz_drift", __name__)


@bp.route("/viz/drift")
@bp.route("/viz/drift/<broker_name>")
def drift(broker_name="ibkr"):
    try:
        broker = get_broker(broker_name)
        symbols = []
        for pos in broker.positions():
            symbols.append({
                "symbol": pos["symbol"],
                "drift": abs(pos.get("unrealized_pl_pct") or 0.0),
                "qty": pos["qty"],
                "avg_entry": pos.get("avg_entry"),
                "market_value": pos.get("market_value"),
            })
        worst = max(symbols, key=lambda s: s["drift"], default=None)
        return jsonify({
            "broker": broker_name,
            "evaluated_at": datetime.now(timezone.utc).isoformat(),
            "soft_band": DRIFT_THRESHOLD,
            "hard_band": None,  # this engine has a single rail
            "max_relative_drift": worst["drift"] if worst else 0.0,
            "max_symbol": worst["symbol"] if worst else None,
            "symbols": symbols,
        })
    except Exception:
        # Don't leak gateway/backend internals to the client; log server-side.
        log.exception("drift retrieval failed (broker=%s)", broker_name)
        return jsonify({"error": "drift retrieval failed"}), 500
