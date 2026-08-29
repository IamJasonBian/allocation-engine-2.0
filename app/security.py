"""Token gate for the routes that can move money.

This service holds live broker credentials (a box-vended Robinhood bearer) and
attaches them server-side, so any unauthenticated route that reaches the broker
is an open relay: the caller supplies no credentials of their own and still
trades the account.

Gated:
  * every mutating request (POST / PUT / PATCH / DELETE) under /api
  * all of /api/robinhood/* including GET, since reading the order book leaks
    account activity

Left open: ordinary GETs (health, status, quotes, positions) — read-only and
relied on by dashboards.

The gate fails closed. With no ``RH_PROXY_TOKEN`` configured the guarded
routes return 503; an unset token never means "open to everyone".
"""

import hmac

from flask import current_app, jsonify, request

WRITE_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})

# Guarded regardless of method — these relay to the auth-service box.
GUARDED_PREFIXES = ("/api/robinhood/",)


def _presented_token() -> str:
    """The caller's token from either accepted header."""
    header = request.headers.get("Authorization", "")
    prefix = "Bearer "
    if header.startswith(prefix):
        return header[len(prefix):]
    return request.headers.get("X-Api-Key", "")


def _needs_token() -> bool:
    path = request.path or ""
    if any(path.startswith(p) for p in GUARDED_PREFIXES):
        return True
    return path.startswith("/api/") and request.method in WRITE_METHODS


def install_write_gate(app) -> None:
    """Attach the gate to `app`. Safe to call once per application."""

    @app.before_request
    def _gate():  # noqa: ANN202 — Flask hook
        if request.method == "OPTIONS" or not _needs_token():
            return None

        expected = current_app.config.get("RH_PROXY_TOKEN", "")
        if not expected:
            return jsonify({
                "error": "endpoint disabled",
                "detail": "RH_PROXY_TOKEN is unset; routes that can reach the "
                          "broker stay closed until it is set",
            }), 503
        if not hmac.compare_digest(_presented_token(), expected):
            return jsonify({
                "error": "unauthorized",
                "detail": "send Authorization: Bearer <RH_PROXY_TOKEN> "
                          "or X-Api-Key",
            }), 401
        return None
