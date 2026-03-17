"""Auth status API — check broker authentication state."""

from flask import Blueprint, jsonify, current_app
from app.brokers import get_broker

bp = Blueprint("auth", __name__)


@bp.route("/auth/status")
@bp.route("/auth/status/<broker_name>")
def auth_status(broker_name=None):
    """Return authentication status for the given broker."""
    broker_name = broker_name or current_app.config["DEFAULT_BROKER"]
    try:
        broker = get_broker(broker_name)
        if hasattr(broker, "auth_status"):
            return jsonify({"broker": broker_name, **broker.auth_status()})
        # Generic fallback — if broker initialized, it's authenticated
        return jsonify({
            "broker": broker_name,
            "authenticated": True,
            "device_challenge_pending": False,
        })
    except Exception as e:
        return jsonify({
            "broker": broker_name,
            "authenticated": False,
            "error": str(e),
        }), 500
