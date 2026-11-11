"""No long options held over the weekend.

From Friday ``cutoff_hour_et`` until Monday 00:00 ET every long option is sold
to close. Planning is stateless and runs every loop pass, so it converges in
two steps per contract:

  1. any open sell-to-close order resting on the contract (a protective stop,
     a take-profit) is cancelled, because Robinhood rejects sells beyond the
     unencumbered quantity;
  2. on a later pass, with nothing resting, a day limit sells the full
     quantity at the mark less ``limit_offset_pct``.

The stop-loss sweep stands down inside the window (``app.utils_shared``) so it
doesn't re-rest stops between the two steps.

Not modeled: exchange holidays (a Friday holiday flattens on a closed market
and the order rests until it can fill). Blocking new opens over the weekend is
an auth-service write rule, not this module.

Skipped on purpose: shorts, and longs on a chain that also holds a short,
since selling the long would leave the short leg naked; positions with no
mark; fractional quantities.
"""

from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

from app.risk.option_stop_loss import OPEN_STATES, _contract_key, _num, snap_down

ET = ZoneInfo("America/New_York")


def in_weekend_window(now: datetime, cutoff_hour_et: float) -> bool:
    """Whether ``now`` falls between the Friday cutoff and Monday 00:00 ET.

    Args:
        now: Timezone-aware time (naive values are treated as UTC).
        cutoff_hour_et: Friday hour in ET, e.g. 15 or 15.5, when the window opens.

    Returns:
        True on Friday at or after the cutoff, and all of Saturday and Sunday.
    """
    if now.tzinfo is None:
        now = now.replace(tzinfo=ZoneInfo("UTC"))
    et = now.astimezone(ET)
    if et.weekday() >= 5:
        return True
    return et.weekday() == 4 and et.hour + et.minute / 60 >= cutoff_hour_et


def _resting_sell_ids(open_orders: list[dict]) -> dict[tuple, list[str]]:
    """Ids of open single-leg sell-to-close orders, per contract.

    Args:
        open_orders: Open option orders (``OrderEvent`` or raw broker dicts).

    Returns:
        Map of contract key to the order ids resting on it.
    """
    out: dict[tuple, list[str]] = {}
    for o in open_orders or []:
        legs = o.get("legs") or []
        if str(o.get("state", "")).lower() not in OPEN_STATES or len(legs) != 1:
            continue
        leg = legs[0]
        if (str(leg.get("side", "")).lower() != "sell"
                or str(leg.get("position_effect", "close")).lower() != "close"):
            continue
        key = _contract_key(leg.get("chain_symbol") or o.get("symbol"), leg.get("option_type"),
                            leg.get("strike"), leg.get("expiration"))
        oid = o.get("id") or o.get("order_id")
        if oid:
            out.setdefault(key, []).append(oid)
    return out


def plan_weekend_flatten(positions: list[dict], open_orders: list[dict], now: datetime,
                         cutoff_hour_et: float,
                         limit_offset_pct: float) -> tuple[list[dict], list[dict], list[dict]]:
    """Decide this pass's weekend-flatten actions.

    Args:
        positions: Dicts from ``broker.options_positions()``.
        open_orders: Open option orders (``OrderEvent`` or raw broker dicts).
        now: Current time, timezone-aware.
        cutoff_hour_et: Friday hour (ET) the weekend window opens.
        limit_offset_pct: How far below the mark the sell limit rests, in percent.

    Returns:
        ``(sells, cancels, skipped)``. A sell has contract, quantity, mark and
        limit_price; a cancel has contract and order_id; a skip has contract
        and reason. All empty outside the weekend window.
    """
    if not in_weekend_window(now, cutoff_hour_et):
        return [], [], []
    short_chains = {str(p.get("chain_symbol", "")).upper()
                    for p in positions or []
                    if str(p.get("position_type", "")).lower() == "short"}
    resting = _resting_sell_ids(open_orders)
    sells, cancels, skipped = [], [], []
    for p in positions or []:
        if str(p.get("position_type", "long")).lower() != "long":
            continue
        contract = {
            "chain_symbol": str(p.get("chain_symbol", "")).upper(),
            "option_type": str(p.get("option_type", "")).lower(),
            "strike": _num(p.get("strike")),
            "expiration": str(p.get("expiration", "")),
        }
        if contract["chain_symbol"] in short_chains:
            skipped.append({"contract": contract, "reason": "paired_with_short_leg"})
            continue
        ids = resting.get(_contract_key(**contract))
        if ids:
            cancels.extend({"contract": contract, "order_id": i} for i in ids)
            continue
        qty = int(_num(p.get("quantity")))
        if qty < 1:
            skipped.append({"contract": contract, "reason": "fractional_qty"})
            continue
        mark = _num(p.get("mark_price"))
        if mark <= 0:
            skipped.append({"contract": contract, "reason": "no_mark"})
            continue
        sells.append({"contract": contract, "quantity": qty, "mark": mark,
                      "limit_price": snap_down(mark * (1 - limit_offset_pct / 100.0))})
    return sells, cancels, skipped
