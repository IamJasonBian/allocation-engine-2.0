"""POST /api/signals/{buy,sell} — open intake for external trade signals.

Notify-only thin wrapper: the signal runs through the handler chain in
app.signals and every notification carries the caller's signal_id. The token
is reported (authorized true/false) but nothing executes today.
"""

import hmac
import math
import uuid
from datetime import datetime, timedelta, timezone

from flask import Blueprint, current_app, jsonify, request

from app.signals import (MARKET_HOURS, MEMORY, SIDES, Signal, build_chain,
                         now_utc, run_chain)

bp = Blueprint("signals", __name__)


def _is_authorized() -> bool:
    required = current_app.config.get("DASHBOARD_REQUEST_TOKEN") or ""
    sent = (request.headers.get("Authorization") or "").removeprefix("Bearer ").strip()
    return bool(required) and hmac.compare_digest(
        sent.encode("utf-8", "replace"), required.encode())


def _bad(msg):
    return jsonify({"error": msg}), 400


@bp.route("/signals/<side>", methods=["POST"])
def trade_signal(side):
    side = side.upper()
    if side not in SIDES:
        return jsonify({"error": "side must be buy or sell"}), 404
    body = request.get_json(silent=True) or {}

    try:
        signal_id = str(uuid.UUID(str(body.get("signal_id") or "")))
    except ValueError:
        return _bad("signal_id (a GUID you generate per signal) is required")

    symbol = str(body.get("symbol") or "").upper()[:12]
    try:
        quantity = float(body.get("quantity"))
        limit_price = None if body.get("limit_price") is None else float(body["limit_price"])
    except (TypeError, ValueError):
        return _bad("quantity (and limit_price, if given) must be numbers")
    if not symbol.isalpha() or not math.isfinite(quantity) or quantity <= 0 \
            or (limit_price is not None and not (math.isfinite(limit_price) and limit_price > 0)):
        return _bad("symbol (letters), positive quantity and limit_price required")

    market_hours = str(body.get("market_hours") or "regular_hours").lower()
    if market_hours not in MARKET_HOURS:
        return _bad(f"market_hours must be one of {list(MARKET_HOURS)}")

    order = body.get("order")
    if order is not None and not isinstance(order, dict):
        return _bad("order must be a Robinhood order object")

    cfg = current_app.config
    received_at = now_utc()
    if body.get("expires_at"):
        try:
            expires_at = datetime.fromisoformat(str(body["expires_at"]))
        except ValueError:
            return _bad("expires_at must be ISO-8601")
        if expires_at.tzinfo is None:
            expires_at = expires_at.replace(tzinfo=timezone.utc)
    else:
        expires_at = received_at + timedelta(seconds=cfg.get("SIGNAL_TTL_SECONDS", 300))

    signal = Signal(
        signal_id=signal_id, side=side, symbol=symbol, quantity=quantity,
        source=str(body.get("source") or "unknown")[:64],
        reason=str(body.get("reason") or "")[:200],
        limit_price=limit_price, market_hours=market_hours,
        received_at=received_at, expires_at=expires_at,
        authorized=_is_authorized(), order=order,
    )
    try:
        chain = build_chain(
            [n.strip() for n in cfg.get("SIGNAL_HANDLERS", "alert").split(",") if n.strip()],
            MEMORY,
            dedupe_ttl=cfg.get("SIGNAL_DEDUPE_SECONDS", 86400),
            cooldown=cfg.get("SIGNAL_COOLDOWN_SECONDS", 300),
        )
        result = run_chain(signal, chain, MEMORY)
    except Exception as e:
        current_app.logger.exception("[signals] chain failed id=%s", signal_id)
        return jsonify({"error": str(e), "signal_id": signal_id}), 500
    current_app.logger.info("[signals] id=%s %s %s status=%s handlers=%s",
                            signal_id, side, symbol, result.get("status"),
                            ",".join(result.get("handlers", [])))
    return jsonify(result), 202
