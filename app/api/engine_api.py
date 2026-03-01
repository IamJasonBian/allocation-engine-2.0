from flask import Blueprint, jsonify
from app.background import get_engine_status, trigger_tick

bp = Blueprint("engine", __name__)


@bp.route("/engine/status")
def engine_status():
    return jsonify(get_engine_status())


@bp.route("/engine/tick", methods=["POST"])
def engine_tick():
    result = trigger_tick()
    return jsonify(result)
