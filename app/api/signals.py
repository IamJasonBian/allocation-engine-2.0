"""POST /api/signals/{buy,sell} — open intake for external trade signals.

No token is required to *send* a signal; the token only decides whether the
signal can execute (see app.signals.select_strategy). Unauthorized signals are
accepted, alerted on, and never traded.
"""

import hmac
import math

from flask import Blueprint, current_app, jsonify, request

from app.brokers import get_broker
from app.signals import SIDES, Signal, select_strategy

bp = Blueprint("signals", __name__)


def _is_authorized() -> bool:
    required = current_app.config.get("DASHBOARD_REQUEST_TOKEN") or ""
    sent = (request.headers.get("Authorization") or "").removeprefix("Bearer ").strip()
    return bool(required) and hmac.compare_digest(
        sent.encode("utf-8", "replace"), required.encode())


@bp.route("/signals/<side>", methods=["POST"])
def trade_signal(side):
    side = side.upper()
    if side not in SIDES:
        return jsonify({"error": "side must be buy or sell"}), 404
    body = request.get_json(silent=True) or {}
    symbol = str(body.get("symbol") or "").upper()[:12]
    source = str(body.get("source") or "unknown")[:64]
    reason = str(body.get("reason") or "")[:200]
    try:
        quantity = float(body.get("quantity"))
        limit_price = None if body.get("limit_price") is None else float(body["limit_price"])
    except (TypeError, ValueError):
        return jsonify({"error": "quantity (and limit_price, if given) must be numbers"}), 400
    if not symbol.isalpha() or not math.isfinite(quantity) or quantity <= 0 \
            or (limit_price is not None and not (math.isfinite(limit_price) and limit_price > 0)):
        return jsonify({"error": "symbol (letters), positive quantity and limit_price required"}), 400

    signal = Signal(side=side, symbol=symbol, quantity=quantity, source=source,
                    reason=reason, limit_price=limit_price,
                    authorized=_is_authorized())
    cfg = current_app.config
    broker_name = cfg["DEFAULT_BROKER"]
    strategy = select_strategy(
        signal,
        mode=cfg.get("SELL_SIGNAL_MODE", "alert"),
        broker_factory=lambda: get_broker(broker_name),
        dry_run=cfg.get("DRY_RUN", True),
    )
    try:
        result = strategy.handle(signal)
    except Exception as e:
        return jsonify({"error": str(e), "strategy": strategy.name}), 500
    return jsonify({"strategy": strategy.name, "authorized": signal.authorized,
                    **result}), 202
