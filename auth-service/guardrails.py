"""Trade-safety guardrails — block destructive actions outside sanctioned shapes.

The service's mandate is narrow: percentage trailing stops, plus plain limit
orders under a notional cap, are the only sanctioned account
mutations. These checks make that structural instead of conventional, on every
surface that could otherwise move money:

  * /orders/trailing_stop (+ /replace) — the payload must actually BE a
    percentage trailing stop, so the allow-listed endpoint can't be used to
    smuggle a plain market/limit order to Robinhood.
  * /orders/limit — the order must be a whole-share limit under
    [limit] max_notional, with a UUID ref_id. The caller picks the ticker.
  * /exec/mcp — tools/call with a destructive tool name (buy/sell/place/
    cancel/…) is refused unless the tool is trailing-stop related or
    explicitly allow-listed via [mcp] allowed_tools. Reads pass through.
  * /exec — shell commands that reference Robinhood order/transfer surfaces
    are refused (accident prevention; the bearer token + firewall remain the
    real gate).

Each check returns None when allowed, or a human-readable reason when blocked.
Callers turn a reason into HTTP 403 GUARDRAIL_BLOCKED and log it.
"""

import re
import uuid
from decimal import Decimal, InvalidOperation

import config

# Verbs that mutate account state. Matched against name tokens, so read tools
# like get_orders / list_positions never trip on their nouns.
DESTRUCTIVE_VERBS = {
    "buy", "sell", "place", "submit", "create", "cancel", "replace", "modify",
    "amend", "edit", "update", "close", "exercise", "liquidate", "transfer",
    "withdraw", "deposit", "execute", "trade", "short", "delete", "set",
}

# Robinhood REST surfaces that place/mutate orders or move money — refused in
# /exec commands regardless of HTTP verb (reads have dedicated endpoints).
_EXEC_BLOCKED_RE = re.compile(
    r"robinhood\.com[^\s]*(orders|transfers|ach|withdraw|deposit|payments)"
    r"|agent\.robinhood\.com",
    re.IGNORECASE,
)


def _tokens(name: str) -> set[str]:
    return {t for t in re.split(r"[^a-z0-9]+", name.lower()) if t}


def _allowed_tools() -> set[str]:
    return {t.strip() for t in config.MCP_ALLOWED_TOOLS.split(",") if t.strip()}


def check_trailing_stop_payload(payload: dict) -> str | None:
    """The only order shape this box may relay: a percentage trailing stop."""
    if payload.get("trigger") != "stop":
        return "payload.trigger must be 'stop' (got %r)" % payload.get("trigger")
    peg = payload.get("trailing_peg")
    if not isinstance(peg, dict) or peg.get("type") != "percentage":
        return "payload.trailing_peg.type must be 'percentage'"
    try:
        pct = float(peg.get("percentage", 0))
    except (TypeError, ValueError):
        pct = 0
    if not 0 < pct <= 100:
        return "trailing_peg.percentage must be in (0, 100]"
    if payload.get("type") != "market":
        return "payload.type must be 'market' (got %r)" % payload.get("type")
    if "price" in payload:
        return "limit 'price' not allowed on a trailing stop"
    if payload.get("side") not in ("buy", "sell"):
        return "payload.side must be 'buy' or 'sell'"
    try:
        qty = float(payload.get("quantity", 0))
    except (TypeError, ValueError):
        qty = 0
    if qty <= 0:
        return "payload.quantity must be > 0"
    return None


def check_limit_order(order: dict) -> str | None:
    """Plain limit orders: whole shares, valid tick, capped notional."""
    if not isinstance(order.get("symbol"), str) or not order["symbol"].strip():
        return "symbol is required"
    if order.get("side") not in ("buy", "sell"):
        return "side must be 'buy' or 'sell'"
    try:
        qty = Decimal(str(order.get("quantity")))
        price = Decimal(str(order.get("limit_price")))
    except InvalidOperation:
        return "quantity and limit_price must be numbers"
    if not (qty.is_finite() and price.is_finite()):
        return "quantity and limit_price must be finite"
    if qty <= 0 or qty != qty.to_integral_value():
        return "quantity must be a positive whole number of shares"
    if price <= 0:
        return "limit_price must be > 0"
    max_places = 2 if price >= 1 else 4
    if -price.normalize().as_tuple().exponent > max_places:
        return "limit_price %s has more than %d decimal places" % (price, max_places)
    notional = qty * price
    if notional > Decimal(str(config.LIMIT_MAX_NOTIONAL)):
        return "notional $%s exceeds [limit] max_notional $%s" % (
            notional, config.LIMIT_MAX_NOTIONAL)
    if order.get("time_in_force", "gfd") not in ("gfd", "gtc"):
        return "time_in_force must be 'gfd' or 'gtc'"
    try:
        uuid.UUID(str(order.get("ref_id")))
    except ValueError:
        return "ref_id must be a UUID (idempotency key; reuse it on retry)"
    return None


def check_mcp_payload(payload: dict) -> str | None:
    """Gate the JSON-RPC relay: only tools/call can mutate, so gate its name."""
    if payload.get("method") != "tools/call":
        return None
    name = str((payload.get("params") or {}).get("name") or "")
    if not name:
        return "tools/call without params.name"
    tokens = _tokens(name)
    if name in _allowed_tools():
        return None
    if "trailing" in tokens:  # trailing-stop tools are the sanctioned writes
        return None
    hits = tokens & DESTRUCTIVE_VERBS
    if hits:
        return "destructive tool %r blocked (matched: %s); add to [mcp] allowed_tools to permit" % (
            name, ", ".join(sorted(hits)))
    return None


def check_exec_command(command) -> str | None:
    """Refuse shell commands that touch Robinhood order/money-movement URLs."""
    text = command if isinstance(command, str) else " ".join(str(a) for a in command)
    m = _EXEC_BLOCKED_RE.search(text)
    if m:
        return "command references a Robinhood trading surface (%r); use the trailing-stop endpoints" % m.group(0)
    return None
