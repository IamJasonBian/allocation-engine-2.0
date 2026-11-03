"""Alert coverage for every open option position, so none goes silently unwatched.

Alert-only: nothing here places or cancels an order.

  * a one-off 🐧 alert when a contract first appears in ``options_positions``,
    and another when it disappears (closed or expired);
  * one alert per contract per day when the P&L tiers (``mental_stops``)
    can't evaluate it: no usable mark, or no purchase price;
  * a coverage line for the SOD/EOD summaries.

State lives in the engine loop alongside the mental-stop ``alerted`` dict, so
a restart starts fresh. The first pass after a restart only records the book
(the Startup summary carries the coverage line), otherwise every deploy would
re-announce every open position as new.
"""

from __future__ import annotations

import logging
from datetime import date

from app.risk.option_stop_loss import OPEN_STATES, contract_key

log = logging.getLogger(__name__)


def _num(v) -> float:
    try:
        return float(v or 0)
    except (TypeError, ValueError):
        return 0.0


def _is_short(p: dict) -> bool:
    return str(p.get("position_type", "")).lower() == "short"


def position_label(p: dict) -> str:
    """Readable contract name, also used as the dedupe key.

    Args:
        p: One dict from ``broker.options_positions()``.

    Returns:
        e.g. ``"MU CALL 95 2026-03-20"``, with `` SHORT`` appended for shorts.
    """
    label = (f"{str(p.get('chain_symbol', '')).upper()} "
             f"{str(p.get('option_type', '')).upper()} "
             f"{_num(p.get('strike')):g} {p.get('expiration', '')}")
    return f"{label} SHORT" if _is_short(p) else label


def _complete(p: dict) -> bool:
    """True when the instrument lookup filled in strike and expiration."""
    return bool(p.get("chain_symbol")) and _num(p.get("strike")) > 0 and bool(p.get("expiration"))


def unmonitored_reason(p: dict) -> str | None:
    """Why the P&L tiers can't evaluate this position, or None when they can.

    Uses the ``mark_ok`` flag when the broker provides it; otherwise a mark
    exactly equal to the purchase price is treated as the purchase-price
    fallback ``options_positions`` logs when market data is missing.

    Args:
        p: One dict from ``broker.options_positions()``.

    Returns:
        A short reason, or None when the position is monitorable.
    """
    if _num(p.get("purchase_price")) <= 0:
        return "no purchase price"
    mark = p.get("mark_price")
    if mark is None:
        return "no mark price"
    if "mark_ok" in p:
        return None if p["mark_ok"] else "mark unavailable (fell back to purchase price)"
    if _num(mark) == _num(p.get("purchase_price")):
        return "mark equals purchase price (likely market-data fallback, P&L reads 0%)"
    return None


def _money(v) -> str:
    return "n/a" if v is None else f"${_num(v):.2f}"


def position_change_alerts(positions: list[dict], seen: dict[str, float] | None
                           ) -> tuple[list[str], dict[str, float]]:
    """One-off alerts for contracts that appeared or disappeared since last pass.

    Args:
        positions: Dicts from ``broker.options_positions()``.
        seen: Label -> quantity from the previous pass; None on the first
            pass after a restart, which records the book without alerting.

    Returns:
        ``(messages, new_seen)``.
    """
    current = {position_label(p): p for p in positions or [] if _complete(p)}
    if seen is None:
        return [], {k: _num(p.get("quantity")) for k, p in current.items()}
    if not positions and seen:
        # options_positions() returns [] on a failed read; don't call that
        # a full close-out.
        log.info("[opt-coverage] empty options read; keeping %d seen positions", len(seen))
        return [], seen

    messages = []
    for label, p in current.items():
        if label not in seen:
            messages.append(
                f"🐧 New option position: {label} x{_num(p.get('quantity')):g}\n"
                f"Buy {_money(p.get('purchase_price'))} → Now {_money(p.get('mark_price'))}"
                f" · DTE {p.get('dte', '?')}")
    new_seen = {k: _num(p.get("quantity")) for k, p in current.items()}
    if len(current) < len(positions):
        # A failed instrument lookup hides which contract a lot is; keep the
        # old entries rather than report them closed.
        for label, qty in seen.items():
            new_seen.setdefault(label, qty)
        return messages, new_seen
    for label, qty in seen.items():
        if label not in current:
            messages.append(f"🐧 Option position gone (closed or expired): {label} x{qty:g}")
    return messages, new_seen


def unmonitored_alerts(positions: list[dict], sent: set[str], today: date | None = None
                       ) -> tuple[list[str], set[str]]:
    """One alert per contract per day for positions the P&L tiers can't see.

    Args:
        positions: Dicts from ``broker.options_positions()``.
        sent: ``"<date>|<label>"`` keys already alerted.
        today: Override for the current date (tests).

    Returns:
        ``(messages, new_sent)``; keys from earlier days are dropped.
    """
    day = (today or date.today()).isoformat()
    new_sent = {k for k in sent if k.startswith(f"{day}|")}
    messages = []
    for p in positions or []:
        reason = unmonitored_reason(p)
        key = f"{day}|{position_label(p)}"
        if reason is None or key in new_sent:
            continue
        messages.append(f"🐧 Not monitored: {position_label(p)} x{_num(p.get('quantity')):g}"
                        f" — {reason}. P&L tier alerts can't evaluate it.")
        new_sent.add(key)
    return messages, new_sent


def _closing_legs(open_orders: list[dict]) -> set[tuple]:
    """``(contract_key, side)`` for every closing leg on an open order.

    Args:
        open_orders: Open option orders (OrderEvent or raw broker dicts).

    Returns:
        Contract keys paired with the leg side (``"sell"`` closes a long,
        ``"buy"`` closes a short).
    """
    out = set()
    for o in open_orders or []:
        if str(o.get("state", "")).lower() not in OPEN_STATES:
            continue
        for leg in o.get("legs") or []:
            if str(leg.get("position_effect", "")).lower() != "close":
                continue
            key = contract_key(leg.get("chain_symbol") or o.get("symbol"),
                               leg.get("option_type"),
                               leg.get("strike") or leg.get("strike_price"),
                               leg.get("expiration") or leg.get("expiration_date"))
            out.add((key, str(leg.get("side", "")).lower()))
    return out


def coverage_line(positions: list[dict], open_orders: list[dict] | None) -> str:
    """Summary line: open, monitored, unmonitored and exit-covered positions.

    Args:
        positions: Dicts from ``broker.options_positions()``.
        open_orders: Open option orders (OrderEvent or raw broker dicts), or
            None when the order read failed (the exit count shows ``?``).

    Returns:
        ``"options: N open · N monitored · N unmonitored · N with exit orders (TP/SL)"``.
    """
    positions = positions or []
    unmonitored = sum(1 for p in positions if unmonitored_reason(p) is not None)
    if open_orders is None:
        with_exit = "?"
    else:
        closing = _closing_legs(open_orders)
        with_exit = sum(
            1 for p in positions
            if (contract_key(p.get("chain_symbol"), p.get("option_type"), p.get("strike"),
                             p.get("expiration")),
                "buy" if _is_short(p) else "sell") in closing)
    return (f"options: {len(positions)} open · {len(positions) - unmonitored} monitored"
            f" · {unmonitored} unmonitored · {with_exit} with exit orders (TP/SL)")
