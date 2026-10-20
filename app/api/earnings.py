"""Earnings API — the custom_overrides windows, for charts that flag prints."""

from flask import Blueprint, jsonify, request

from app.overrides import CUSTOM_OVERRIDES

bp = Blueprint("earnings", __name__)


@bp.route("/earnings")
def earnings():
    """List earnings windows, optionally filtered by ``symbols`` (comma-separated)."""
    symbols_str = request.args.get("symbols", "")
    wanted = {s.strip().upper() for s in symbols_str.split(",") if s.strip()}
    windows = [
        {
            "symbol": row.symbol,
            "start_time": row.start_time.isoformat(),
            "end_time": row.end_time.isoformat(),
        }
        for row in CUSTOM_OVERRIDES
        if not wanted or row.symbol.upper() in wanted
    ]
    return jsonify({"windows": windows})
