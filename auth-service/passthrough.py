"""Thin Robinhood relay: /rh/<path> -> https://api.robinhood.com/<path>.

GETs pass through; Robinhood's status + body are relayed as-is, so Robinhood
rejects bad shapes. Writes must match WRITE_RULES, which check only what
Robinhood would accept but we don't want. Writes dry-run unless X-Dry-Run: false.
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


def _check_notional(body: dict, multiplier: Decimal, cap: float, name: str) -> str | None:
    """Fail closed: if quantity/price don't parse, the cap can't be checked."""
    qty, price = _dec(body.get("quantity")), _dec(body.get("price"))
    if qty is None or price is None:
        return "quantity and price must be numbers (needed for the %s cap)" % name
    notional = abs(qty * price * multiplier)
    if notional > Decimal(str(cap)):
        return "notional $%s exceeds [passthrough] %s $%s" % (notional, name, cap)
    return None


def check_equity_order(body: dict) -> str | None:
    """POST /orders/ — notional cap + idempotency key; Robinhood validates the rest."""
    return (_check_notional(body, Decimal(1), config.RH_EQUITY_MAX_NOTIONAL,
                            "equity_max_notional")
            or _check_ref_id(body))


def _leg_is(leg, side: str, effect: str) -> bool:
    return (isinstance(leg, dict)
            and str(leg.get("side", "")).lower() == side
            and str(leg.get("position_effect", "")).lower() == effect)


def _ratio(leg) -> Decimal | None:
    r = _dec(leg.get("ratio_quantity", 1))
    return r if r is not None and r > 0 else None


def check_option_order(body: dict) -> str | None:
    """POST /options/orders/ — notional cap, no naked short opens, idempotency key.

    An order whose legs all close is exempt from the cap: it can only shrink
    exposure. Sold-to-open ratio must be covered by bought-to-open ratio.
    """
    legs = body.get("legs") if isinstance(body.get("legs"), list) else []
    closes_only = bool(legs) and all(
        isinstance(l, dict) and str(l.get("position_effect", "")).lower() == "close"
        for l in legs)
    if not closes_only:
        reason = _check_notional(body, _OPTIONS_MULTIPLIER, config.RH_OPTIONS_MAX_NOTIONAL,
                                 "options_max_notional")
        if reason:
            return reason
    opens = [l for l in legs if _leg_is(l, "sell", "open") or _leg_is(l, "buy", "open")]
    if any(_ratio(l) is None for l in opens):
        return "ratio_quantity must be a positive number (needed for the naked-short check)"
    sold = sum((_ratio(l) for l in opens if _leg_is(l, "sell", "open")), Decimal(0))
    bought = sum((_ratio(l) for l in opens if _leg_is(l, "buy", "open")), Decimal(0))
    if sold > bought:
        return ("sell-to-open ratio %s exceeds bought-to-open ratio %s in the same order "
                "(naked short) is blocked" % (sold, bought))
    return _check_ref_id(body)


# (method, path regex) -> validator. Anything not listed is refused for writes.
WRITE_RULES = [
    ("POST", re.compile(r"^/orders/$"), check_equity_order),
    ("POST", re.compile(r"^/options/orders/$"), check_option_order),
    ("POST", re.compile(r"^/(options/)?orders/[0-9a-f-]+/cancel/$"), None),  # cancels
]


def check_write(method: str, path: str, body: dict) -> str | None:
    """Return why a write must not be forwarded, or None to allow it."""
    bare = path.partition("?")[0]
    for rule_method, pattern, validator in WRITE_RULES:
        if method == rule_method and pattern.match(bare):
            return validator(body if isinstance(body, dict) else {}) if validator else None
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
