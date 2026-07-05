"""Trade-safety guardrails — block destructive actions outside trailing stops.

The service's mandate is narrow: percentage trailing-stop orders are the only
sanctioned account mutation. These checks make that structural instead of
conventional, on all three surfaces that could otherwise move money:

  * /orders/trailing_stop (+ /replace) — the payload must actually BE a
    percentage trailing stop, so the allow-listed endpoint can't be used to
    smuggle a plain market/limit order to Robinhood.
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
