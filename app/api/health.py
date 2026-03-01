from datetime import datetime, timezone
from flask import Blueprint, jsonify, current_app

bp = Blueprint("health", __name__)


@bp.route("/health")
def health():
    config = current_app.config
    return jsonify({
        "status": "ok",
        "service": "allocation-engine-2.0",
        "version": "2.0.0",
        "enabled_brokers": config["ENABLED_BROKERS"],
        "default_broker": config["DEFAULT_BROKER"],
        "engine_enabled": config["ENGINE_ENABLED"],
        "dry_run": config["DRY_RUN"],
        "timestamp": datetime.now(timezone.utc).isoformat(),
    })
