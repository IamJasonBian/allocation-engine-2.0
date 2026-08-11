"""Wheel-lanes view — per-underlying option/share state, phase-labelled.

Serves the "strategy's own life cycle" chart: the current book grouped by
underlying, with shares and option legs kept separate so short puts, held
shares, and covered calls each land in their lane. Phase is derived from the
legs present; span history comes from polling this endpoint over time.

GET /api/viz/wheel-lanes          → ibkr
GET /api/viz/wheel-lanes/<broker> → 400 unless the broker reports option legs
"""

from datetime import datetime, timezone

import logging

from flask import Blueprint, jsonify

from app.brokers import get_broker

log = logging.getLogger(__name__)
bp = Blueprint("viz_wheel_lanes", __name__)


def _phase(shares, options):
    short_puts = [o for o in options if o["right"] == "P" and o["qty"] < 0]
    short_calls = [o for o in options if o["right"] == "C" and o["qty"] < 0]
    has_shares = shares is not None and shares["qty"] > 0
    if has_shares and short_calls:
        return "covered_call"
    if short_puts and not has_shares:
        return "cash_secured_put"
    if has_shares and not options:
        return "shares"
    return "mixed"


@bp.route("/viz/wheel-lanes")
@bp.route("/viz/wheel-lanes/<broker_name>")
def wheel_lanes(broker_name="ibkr"):
    try:
        broker = get_broker(broker_name)
        if not hasattr(broker, "portfolio_detail"):
            return jsonify({
                "error": f"wheel-lanes needs option-level portfolio detail; "
                         f"broker '{broker_name}' does not expose it"
            }), 400

        by_symbol = {}
        for row in broker.portfolio_detail():
            by_symbol.setdefault(row["symbol"], []).append(row)

        lanes = []
        for symbol, rows in sorted(by_symbol.items()):
            shares = next((r for r in rows if r["sec_type"] == "STK"), None)
            options = [r for r in rows if r["sec_type"] == "OPT"]
            lanes.append({
                "symbol": symbol,
                "shares": shares,
                "options": options,
                "phase": _phase(shares, options),
            })

        return jsonify({
            "broker": broker_name,
            "as_of": datetime.now(timezone.utc).isoformat(),
            "count": len(lanes),
            "lanes": lanes,
        })
    except Exception:
        log.exception("wheel-lanes retrieval failed (broker=%s)", broker_name)
        return jsonify({"error": "wheel-lanes retrieval failed"}), 500
