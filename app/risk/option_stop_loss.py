"""Plan protective sell-to-close stop-limit orders for long option positions.

Pure planning only: placement lives on the broker
(``RobinhoodTrader.place_option_stop_loss``) and gating in
``app.utils_shared.maybe_option_stop_loss_sweep``.

For each long contract the stop triggers at ``entry * (1 - stop_pct/100)`` and
rests a limit ``limit_offset_pct`` below the trigger, so a fast drop still has
room to fill. Prices snap down to ticks valid for every underlying ($0.05
under $3, $0.10 at or above), penny-program or not.

Skipped on purpose:
  * shorts, fractional lots, and lots without an RH purchase price;
  * a long on any chain that also holds a short (any type or expiry, so
    verticals, calendars and diagonals all count): stopping the long out would
    leave the short leg naked;
  * a stop percent outside (0, 100);
  * a long whose current mark is missing or at/below the stop, since that
    stop would trigger immediately;
  * contracts whose quantity is fully held by open sell-to-close orders
    (stops or e.g. a resting take-profit). Only the free remainder gets a
    stop: Robinhood rejects sells beyond the unencumbered quantity, and it has
    no OCO for options.
"""

from __future__ import annotations

import math

from app.risk.option_stop_mapping import option_stop_percent

OPEN_STATES = {"queued", "confirmed", "partially_filled", "pending", "unconfirmed"}


def _num(v) -> float:
    """Coerce a broker field to float, treating blanks and junk as zero.

    Args:
        v: Raw value (str, number, or None).

    Returns:
        The value as a float, or 0.0 when it can't be parsed.
    """
    try:
        return float(v or 0)
    except (TypeError, ValueError):
        return 0.0


def snap_down(price: float) -> float:
    """Round a premium down to a tick valid for any underlying.

    Uses the non-penny-program increments ($0.05 under $3, $0.10 at or
    above), which every option class accepts.

    Args:
        price: Premium per share in USD.

    Returns:
        The price on a valid tick, never below $0.05.
    """
    tick = 0.05 if price < 3 else 0.10
    snapped = math.floor(round(price / tick, 6)) * tick
    return max(round(snapped, 2), 0.05)


def stop_prices(entry: float, stop_pct: float, limit_offset_pct: float) -> tuple[float, float]:
    """Stop trigger and limit for a long option bought at ``entry``.

    Args:
        entry: Purchase premium per share.
        stop_pct: Drawdown percent that triggers the stop.
        limit_offset_pct: How far below the trigger the limit rests, in percent.

    Returns:
        ``(stop_price, limit_price)``, both on valid ticks, limit <= stop.
    """
    stop = snap_down(entry * (1 - stop_pct / 100.0))
    limit = snap_down(stop * (1 - limit_offset_pct / 100.0))
    return stop, min(limit, stop)


def _contract_key(chain_symbol, option_type, strike, expiration) -> tuple:
    """Normalized key identifying one option contract.

    Args:
        chain_symbol: Underlying chain symbol, any case.
        option_type: ``"call"`` or ``"put"``, any case.
        strike: Strike price.
        expiration: Expiration date string (YYYY-MM-DD).

    Returns:
        ``(chain_symbol, option_type, strike, expiration)`` normalized for lookup.
    """
    return (str(chain_symbol or "").upper(), str(option_type or "").lower(),
            round(_num(strike), 4), str(expiration or ""))


def _sell_close_orders(open_orders: list[dict]) -> dict[tuple, dict]:
    """Sum open single-leg sell-to-close quantity per contract.

    Args:
        open_orders: Open option orders (OrderEvent or raw broker dicts).

    Returns:
        Map of contract key to ``{"qty": unfilled quantity held, "has_stop":
        whether any of those orders is a stop}``.
    """
    held: dict[tuple, dict] = {}
    for o in open_orders or []:
        if str(o.get("state", "")).lower() not in OPEN_STATES:
            continue
        legs = o.get("legs") or []
        if len(legs) != 1:
            continue                    # multi-leg orders don't hold a single contract
        leg = legs[0]
        if str(leg.get("side", "")).lower() != "sell":
            continue
        if str(leg.get("position_effect", "close")).lower() != "close":
            continue
        key = _contract_key(leg.get("chain_symbol") or o.get("symbol"),
                            leg.get("option_type"), leg.get("strike"),
                            leg.get("expiration"))
        slot = held.setdefault(key, {"qty": 0.0, "has_stop": False})
        qty = _num(o.get("quantity")) - _num(o.get("filled_quantity"))
        slot["qty"] += max(qty, 0.0)
        if str(o.get("trigger", "")).lower() == "stop":
            slot["has_stop"] = True
    return held


def plan_option_stop_losses(positions: list[dict], open_orders: list[dict],
                            default_stop_pct: float,
                            limit_offset_pct: float) -> tuple[list[dict], list[dict]]:
    """Decide which long option positions need a protective stop-limit.

    Args:
        positions: Dicts from ``broker.options_positions()``.
        open_orders: Open option orders (``OrderEvent`` dicts or raw
            ``broker.options_orders()`` dicts; both carry legs/trigger/state).
        default_stop_pct: Drawdown percent for symbols without an override.
        limit_offset_pct: Limit distance below the trigger, in percent.

    Returns:
        ``(orders, skipped)``. Each order has contract, quantity, entry,
        stop_pct, stop_price and limit_price; each skip has contract and reason.
    """
    short_chains = {str(p.get("chain_symbol", "")).upper()
                    for p in positions or []
                    if str(p.get("position_type", "")).lower() == "short"}
    held = _sell_close_orders(open_orders)
    orders, skipped = [], []

    for p in positions or []:
        if str(p.get("position_type", "long")).lower() != "long":
            continue
        contract = {
            "chain_symbol": str(p.get("chain_symbol", "")).upper(),
            "option_type": str(p.get("option_type", "")).lower(),
            "strike": _num(p.get("strike")),
            "expiration": str(p.get("expiration", "")),
        }
        if (not contract["chain_symbol"] or contract["option_type"] not in ("call", "put")
                or contract["strike"] <= 0 or not contract["expiration"]):
            skipped.append({"contract": contract, "reason": "incomplete_contract"})
            continue
        if contract["chain_symbol"] in short_chains:
            skipped.append({"contract": contract, "reason": "paired_with_short_leg"})
            continue
        entry = _num(p.get("purchase_price"))
        if entry <= 0:
            skipped.append({"contract": contract, "reason": "no_purchase_price"})
            continue
        slot = held.get(_contract_key(**contract), {"qty": 0.0, "has_stop": False})
        free = int(_num(p.get("quantity")) - slot["qty"])
        if free < 1:
            if slot["has_stop"]:
                reason = "stop_already_open"
            elif slot["qty"] > 0:
                reason = "held_by_open_sell"
            else:
                reason = "fractional_qty"
            skipped.append({"contract": contract, "reason": reason})
            continue
        pct = option_stop_percent(contract["chain_symbol"], default_stop_pct)
        if not 0 < pct < 100:
            skipped.append({"contract": contract, "reason": "invalid_stop_pct"})
            continue
        stop, limit = stop_prices(entry, pct, limit_offset_pct)
        mark = p.get("mark_price")
        if mark is None or _num(mark) <= stop:
            skipped.append({"contract": contract, "reason": "mark_at_or_below_stop"})
            continue
        orders.append({"contract": contract, "quantity": free, "entry": entry,
                       "stop_pct": pct, "stop_price": stop, "limit_price": limit})
    return orders, skipped
