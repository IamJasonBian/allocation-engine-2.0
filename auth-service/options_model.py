"""Single-leg option limit intents — contract identity vs trade action.

Robinhood option orders are inherently multi-leg at the API layer (`legs[]`
with per-leg `side`, `position_effect`, and `option` URL), plus order-level
economics (`price` = limit premium, `quantity` = contracts, `direction` =
debit/credit). Callers send a *single-leg intent*; the box resolves the
contract URL and builds that shape.

POST /orders/options/limit body::

    {
      "order": {
        "contract": {
          "chain_symbol": "MU",
          "option_type": "call",
          "strike": 95,
          "expiration": "2026-03-20"
        },
        "action": "buy_to_open",
        "quantity": 1,
        "limit_price": "2.50",
        "time_in_force": "gtc",
        "ref_id": "<uuid>"
      },
      "dry_run": true
    }

Engine runtime ``option_orders`` use the same field names but flat (no
``contract`` wrapper). ``flatten_runtime_order`` adapts them for the box.
"""

from __future__ import annotations

import re
from decimal import Decimal, InvalidOperation

_EXPIRATION_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")

# Standard four opening/closing actions → Robinhood leg + premium direction.
OPTION_ACTIONS: dict[str, dict[str, str]] = {
    "buy_to_open": {"side": "buy", "position_effect": "open", "direction": "debit"},
    "sell_to_open": {"side": "sell", "position_effect": "open", "direction": "credit"},
    "buy_to_close": {"side": "buy", "position_effect": "close", "direction": "debit"},
    "sell_to_close": {"side": "sell", "position_effect": "close", "direction": "credit"},
}


def rh_leg_from_action(action: str) -> dict[str, str] | None:
    return OPTION_ACTIONS.get(action)


def flatten_runtime_order(order: dict) -> dict:
    """Wrap flat engine/runtime option_orders into the box intent shape."""
    if isinstance(order.get("contract"), dict):
        return order
    contract = {
        "chain_symbol": order["chain_symbol"],
        "option_type": order["option_type"],
        "strike": order["strike"],
        "expiration": order["expiration"],
    }
    side = str(order.get("side", "")).lower()
    effect = order.get("position_effect")
    if effect:
        action = f"{side}_to_{effect}"
    else:
        action = order.get("action") or f"{side}_to_open"
    out = {
        "contract": contract,
        "action": action,
        "quantity": order["quantity"],
        "limit_price": order["limit_price"],
        "time_in_force": order.get("time_in_force", "gtc"),
        "ref_id": order["ref_id"],
    }
    return out


def parse_contract(contract: dict) -> tuple[dict | None, str | None]:
    """Validate and normalize contract fields. Returns (parsed, error)."""
    if not isinstance(contract, dict):
        return None, "contract must be an object"
    chain = contract.get("chain_symbol")
    if not isinstance(chain, str) or not chain.strip():
        return None, "contract.chain_symbol is required"
    opt_type = str(contract.get("option_type") or "").lower()
    if opt_type not in ("call", "put"):
        return None, "contract.option_type must be 'call' or 'put'"
    exp = contract.get("expiration")
    if not isinstance(exp, str) or not _EXPIRATION_RE.match(exp.strip()):
        return None, "contract.expiration must be YYYY-MM-DD"
    try:
        strike = Decimal(str(contract.get("strike")))
    except InvalidOperation:
        return None, "contract.strike must be a number"
    if not strike.is_finite() or strike <= 0:
        return None, "contract.strike must be > 0"
    return {
        "chain_symbol": chain.strip().upper(),
        "option_type": opt_type,
        "strike": strike,
        "expiration": exp.strip(),
    }, None
