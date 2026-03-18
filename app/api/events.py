"""Order events API — serves historical equity and option order events from S3."""

from flask import Blueprint, jsonify, request

from app.s3_store import get_events, list_event_dates

bp = Blueprint("events", __name__)


@bp.route("/events")
@bp.route("/events/<date>")
def order_events(date=None):
    """Return order events for a given date (default: today).

    Query params:
        asset_type: "equity" or "option" (omit for both)
        limit: max rows (default 500)

    GET /api/events              → today's events
    GET /api/events/2026-03-18   → events for that date
    GET /api/events?asset_type=option  → only option events today
    """
    asset_type = request.args.get("asset_type")
    limit = int(request.args.get("limit", "500"))

    events = get_events(date=date, asset_type=asset_type, limit=limit)

    return jsonify({
        "date": date,
        "asset_type": asset_type,
        "count": len(events),
        "events": events,
    })


@bp.route("/events/dates")
def event_dates():
    """List dates that have event data in S3.

    Query params:
        days: how many dates to return (default 30)

    GET /api/events/dates → ["2026-03-18", "2026-03-17", ...]
    """
    days = int(request.args.get("days", "30"))
    dates = list_event_dates(days=days)
    return jsonify({"dates": dates, "count": len(dates)})
