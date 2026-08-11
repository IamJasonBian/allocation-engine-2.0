"""Dashboard page — replica of the 5thstreetcapital.org views.

Static HTML served by this API service; the page itself reads the same public
Trading DB functions the live dashboard consumes (order-book-snapshot,
db-bot-activity), so it needs no broker session and works anywhere the
functions are reachable. `?db=<base>` points it at a different functions host.
"""

from pathlib import Path

from flask import Blueprint, Response

bp = Blueprint("dashboard", __name__)

_PAGE = Path(__file__).resolve().parents[2] / "dashboard" / "index.html"


@bp.route("/dashboard")
def dashboard() -> Response:
    return Response(_PAGE.read_text(), mimetype="text/html")
