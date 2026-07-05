"""Robinhood calls routed through the external auth-service.

- GET/POST /api/robinhood/trailing-stop  — allow-listed direct calls (RH session)
- POST     /api/robinhood/mcp            — JSON-RPC passthrough to Robinhood MCP
"""

from flask import Blueprint, jsonify, request, current_app

from app.auth_service_client import (
    AuthServiceClient,
    AuthServiceNotConfigured,
    AuthServiceError,
    OTPRequired,
    build_trailing_stop_payload,
)

bp = Blueprint("robinhood_proxy", __name__)


def _handle(fn):
    """Run an auth-service call and map its errors to HTTP responses."""
    try:
        return jsonify(fn())
    except AuthServiceNotConfigured as e:
        return jsonify({"error": "auth-service not configured", "detail": str(e)}), 503
    except OTPRequired as e:
        return jsonify({"error": "OTP needed", "otp_needed": True, "detail": str(e)}), 409
    except AuthServiceError as e:
        return jsonify({"error": "auth-service call failed", "detail": str(e)}), 502


@bp.route("/robinhood/trailing-stop", methods=["GET"])
def get_trailing_stop():
    """Read active percentage trailing-stop orders (allow-listed direct call)."""
    client = AuthServiceClient()
    return _handle(client.get_trailing_stop_orders)


@bp.route("/robinhood/trailing-stop", methods=["POST"])
def place_trailing_stop():
    """Build and place a trailing-stop order (allow-listed direct call).

    Body JSON — either a pre-built ``payload`` object, or the fields to build one:
        account, instrument, symbol, side, quantity, trail_percent,
        stop_price (optional), time_in_force (optional)
    Plus optional ``dry_run`` (defaults to the service DRY_RUN setting).
    """
    body = request.get_json(silent=True) or {}
    dry_run = body.get("dry_run", current_app.config.get("DRY_RUN", True))

    payload = body.get("payload")
    if payload is None:
        required = ("account", "instrument", "symbol", "side", "quantity", "trail_percent")
        missing = [f for f in required if body.get(f) is None]
        if missing:
            return jsonify({
                "error": "provide a 'payload' object or the fields to build one",
                "missing": missing,
            }), 400
        payload = build_trailing_stop_payload(
            account_url=body["account"],
            instrument_url=body["instrument"],
            symbol=body["symbol"],
            side=body["side"],
            quantity=body["quantity"],
            trail_percent=body["trail_percent"],
            stop_price=body.get("stop_price"),
            time_in_force=body.get("time_in_force", "gtc"),
        )

    client = AuthServiceClient()
    return _handle(lambda: client.place_trailing_stop(payload, dry_run=dry_run))


@bp.route("/robinhood/mcp", methods=["POST"])
def run_mcp():
    """Relay a JSON-RPC call to the official Robinhood MCP via the auth-service.

    Body JSON — either a raw JSON-RPC ``payload``:
        {"payload": {"jsonrpc":"2.0","id":1,"method":"tools/list"}}
    or the convenience form:
        {"method": "tools/call", "params": {...}, "id": 1}
    Optional ``session_id`` propagates the MCP's Mcp-Session-Id.
    """
    body = request.get_json(silent=True) or {}
    session_id = body.get("session_id")
    client = AuthServiceClient()

    payload = body.get("payload")
    if payload is not None:
        return _handle(lambda: client.mcp_relay(payload, session_id=session_id))

    method = body.get("method")
    if not method:
        return jsonify({"error": "provide a JSON-RPC 'payload' or a 'method'"}), 400
    return _handle(lambda: client.mcp_call(
        method, params=body.get("params"),
        req_id=body.get("id", 1), session_id=session_id))
