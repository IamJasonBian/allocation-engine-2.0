"""Thin Robinhood relay: /rh/<path> -> https://api.robinhood.com/<path>.

The box holds the session; callers own the request shape. Reads are forwarded
untouched, and Robinhood's own status + body come back verbatim, so a bad shape
is rejected by Robinhood rather than re-validated here.

Writes are the only sensitive surface. A write is forwarded only when its path
matches a rule below; every other non-GET is refused. Rules check just what
protects the account (notional caps, no naked short opens, idempotency key) —
not the full Robinhood schema.

Writes are dry-run unless the caller sends ``X-Dry-Run: false``.
"""

import re
import uuid
from decimal import Decimal, InvalidOperation

import requests

import config

BASE = "https://api.robinhood.com"
PREFIX = "/rh"
_OPTIONS_MULTIPLIER = Decimal(100)

# Paths must stay on api.robinhood.com: no scheme/host smuggling, no traversal.
_SAFE_PATH = re.compile(r"^/[A-Za-z0-9_\-./]*$")


def upstream_path(request_path: str) -> str | None:
    """Map '/rh/options/orders/?x=1' to '/options/orders/?x=1', or None if unsafe."""
    if not request_path.startswith(PREFIX + "/"):
        return None
    rest = request_path[len(PREFIX):]
    path, _, query = rest.partition("?")
    if not _SAFE_PATH.match(path) or ".." in path or "//" in path:
        return None
    return rest if not query else f"{path}?{query}"


def _dec(value) -> Decimal | None:
    try:
        d = Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None
    return d if d.is_finite() else None


def _check_ref_id(body: dict) -> str | None:
    try:
        uuid.UUID(str(body.get("ref_id")))
    except ValueError:
        return "ref_id must be a UUID (idempotency key; reuse it on retry)"
    return None


def check_equity_order(body: dict) -> str | None:
    """POST /orders/ — priced, positive, under the equity notional cap."""
    if body.get("side") not in ("buy", "sell"):
        return "side must be 'buy' or 'sell'"
    qty, price = _dec(body.get("quantity")), _dec(body.get("price"))
    if qty is None or qty <= 0:
        return "quantity must be > 0"
    if price is None or price <= 0:
        return "price must be > 0 (also used for the notional cap)"
    notional = qty * price
    if notional > Decimal(str(config.RH_EQUITY_MAX_NOTIONAL)):
        return "notional $%s exceeds [passthrough] equity_max_notional $%s" % (
            notional, config.RH_EQUITY_MAX_NOTIONAL)
    return _check_ref_id(body)


def check_option_order(body: dict) -> str | None:
    """POST /options/orders/ — whole contracts, capped premium, no naked short opens."""
    legs = body.get("legs")
    if not isinstance(legs, list) or not 1 <= len(legs) <= 4:
        return "legs must be a list of 1-4 legs"
    for leg in legs:
        if not isinstance(leg, dict) or leg.get("side") not in ("buy", "sell") \
                or leg.get("position_effect") not in ("open", "close"):
            return "each leg needs side buy|sell and position_effect open|close"
    qty, price = _dec(body.get("quantity")), _dec(body.get("price"))
    if qty is None or qty <= 0 or qty != qty.to_integral_value():
        return "quantity must be a positive whole number of contracts"
    if price is None or price <= 0:
        return "price must be > 0"
    notional = qty * price * _OPTIONS_MULTIPLIER
    if notional > Decimal(str(config.RH_OPTIONS_MAX_NOTIONAL)):
        return "notional $%s exceeds [passthrough] options_max_notional $%s" % (
            notional, config.RH_OPTIONS_MAX_NOTIONAL)
    short_opens = [l for l in legs if l["side"] == "sell" and l["position_effect"] == "open"]
    long_opens = [l for l in legs if l["side"] == "buy" and l["position_effect"] == "open"]
    if short_opens and not long_opens:
        return "sell-to-open without a bought leg in the same order (naked short) is blocked"
    return _check_ref_id(body)


def _always_ok(_body: dict) -> str | None:
    return None


# (method, path regex) -> validator. Anything not listed is refused for writes.
WRITE_RULES: list[tuple[str, re.Pattern, callable]] = [
    ("POST", re.compile(r"^/orders/$"), check_equity_order),
    ("POST", re.compile(r"^/options/orders/$"), check_option_order),
    # Cancels only reduce exposure.
    ("POST", re.compile(r"^/orders/[0-9a-f-]+/cancel/$"), _always_ok),
    ("POST", re.compile(r"^/options/orders/[0-9a-f-]+/cancel/$"), _always_ok),
]


def check_write(method: str, path: str, body: dict) -> str | None:
    """Return why a write must not be forwarded, or None to allow it."""
    bare = path.partition("?")[0]
    for rule_method, pattern, validator in WRITE_RULES:
        if method == rule_method and pattern.match(bare):
            return validator(body if isinstance(body, dict) else {})
    return "%s %s is not an allowed write; only order placement and cancels are relayed" % (
        method, bare)


def forward(session, method: str, path: str, body: dict | None,
            timeout: int = 15) -> tuple[int, object]:
    """Send the request to Robinhood and return (status, parsed body or text)."""
    resp = requests.request(method, BASE + path, headers=session.headers(),
                            json=body if method != "GET" else None, timeout=timeout)
    try:
        return resp.status_code, resp.json()
    except ValueError:
        return resp.status_code, {"_raw": (resp.text or "")[:2000]}
