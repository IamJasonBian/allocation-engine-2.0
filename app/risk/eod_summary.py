"""End-of-trading-day Telegram summary: open orders and option book snapshot.

Fires once per US market day after ``EOD_SUMMARY_ET`` (default 16:05 America/New_York),
on weekdays only. Dedupe key is ``YYYY-MM-DD@eod`` in the worker's in-memory ``sent`` set
(cleared on process restart).
"""

from __future__ import annotations

import os
from datetime import date, datetime, time as dtime
from zoneinfo import ZoneInfo

from app.enums import OPEN_STATES

_ET = ZoneInfo("America/New_York")
_DEFAULT_EOD = dtime(16, 5)


def _eod_time() -> dtime:
    raw = os.getenv("EOD_SUMMARY_ET", "16:05")
    hour, minute = (int(x) for x in raw.split(":", 1))
    return dtime(hour, minute)


_OPEN = {s.value for s in OPEN_STATES}


def _is_open(state: str) -> bool:
    return (state or "").lower() in _OPEN


def normalize_equity_order(o: dict) -> dict:
    """RH open stock orders use ``status`` and ``qty``; EOD format expects ``state`` / ``quantity``."""
    out = dict(o)
    out.setdefault("state", out.get("status", ""))
    out.setdefault("quantity", out.get("qty", 0))
    return out


def normalize_option_order(o: dict) -> dict:
    """Ensure option order dicts from OrderEvent or RH have ``state`` and leg strike keys."""
    out = dict(o)
    if out.get("state") in (None, ""):
        out["state"] = out.get("status", "")
    legs = []
    for leg in out.get("legs") or []:
        lg = dict(leg)
        lg.setdefault("strike", lg.get("strike_price"))
        lg.setdefault("expiration", lg.get("expiration_date"))
        lg.setdefault("chain_symbol", out.get("symbol") or lg.get("chain_symbol"))
        legs.append(lg)
    if legs:
        out["legs"] = legs
    return out


def open_equity_orders(orders: list[dict]) -> list[dict]:
    """Pending stock orders only (always evaluated for EOD)."""
    return [
        normalize_equity_order(o)
        for o in orders
        if _is_open(str((o.get("state") or o.get("status") or "")))
    ]


def open_option_orders(orders: list[dict]) -> list[dict]:
    """Pending option orders only (spreads, single-leg opens, etc.)."""
    return [
        normalize_option_order(o)
        for o in orders
        if _is_open(str(o.get("state") or o.get("status") or ""))
    ]


def _fmt_option_order(o: dict) -> str:
    legs = o.get("legs") or []
    sym = o.get("symbol") or (legs[0].get("chain_symbol") if legs else "?")
    state = o.get("state", "?")
    qty = o.get("quantity") or o.get("filled_quantity") or 0
    strat = o.get("opening_strategy") or o.get("direction") or "option"
    price = o.get("limit_price")
    price_s = f" @ ${price:.2f}" if price else ""
    if len(legs) >= 2:
        strikes = sorted(
            float(leg.get("strike") or leg.get("strike_price") or 0) for leg in legs
        )
        exp = legs[0].get("expiration") or legs[0].get("expiration_date") or "?"
        return (
            f"• {sym} {strat} {strikes[0]:g}/{strikes[1]:g} {exp} x{qty:g}{price_s} ({state})"
        )
    return f"• {sym} {strat} x{qty:g}{price_s} ({state})"


def _fmt_equity_order(o: dict) -> str:
    sym = o.get("symbol", "?")
    state = o.get("state") or o.get("status") or "?"
    qty = o.get("quantity") or o.get("qty") or 0
    side = (o.get("side") or "?").upper()
    kind = o.get("order_type") or o.get("type") or "order"
    stop = o.get("stop_price")
    if stop:
        return f"• {sym} {side} stop ${float(stop):.2f} x{qty:g} ({state})"
    limit = o.get("limit_price")
    if limit:
        return f"• {sym} {side} limit ${float(limit):.2f} x{qty:g} ({state})"
    return f"• {sym} {side} {kind} x{qty:g} ({state})"


def _fmt_option_position(p: dict) -> str:
    sym = p.get("chain_symbol", "?")
    kind = str(p.get("option_type", "?")).upper()
    strike = p.get("strike", "?")
    exp = p.get("expiration", "?")
    qty = p.get("quantity", 0)
    mark = p.get("mark_price")
    cost = p.get("purchase_price") or p.get("avg_price")
    px = f" mark ${mark:.2f}" if mark is not None else ""
    if cost:
        px = f" cost ${float(cost):.2f} → mark ${float(mark or cost):.2f}"
    return f"• {sym} {kind} {strike} {exp} x{qty:g}{px}"


def build_eod_message(
    *,
    option_positions: list[dict],
    equity_orders: list[dict],
    option_orders: list[dict],
    as_of: date,
) -> str:
    """Format the EOD body. Always includes all three sections (counts may be zero)."""
    o_opts = open_option_orders(option_orders)
    o_eq = open_equity_orders(equity_orders)

    lines = [f"📋 EOD {as_of.isoformat()} (ET) — pending orders & open options"]

    lines.append(f"\nPending option orders ({len(o_opts)})")
    if o_opts:
        for o in sorted(o_opts, key=lambda x: (x.get("symbol") or "", x.get("created_at") or "")):
            lines.append(_fmt_option_order(o))
    else:
        lines.append("• (none)")

    lines.append(f"\nPending equity orders ({len(o_eq)})")
    if o_eq:
        for o in sorted(o_eq, key=lambda x: x.get("symbol", "")):
            lines.append(_fmt_equity_order(o))
    else:
        lines.append("• (none)")

    lines.append(f"\nOpen option positions ({len(option_positions)})")
    if option_positions:
        for p in sorted(option_positions, key=lambda x: x.get("chain_symbol", "")):
            lines.append(_fmt_option_position(p))
    else:
        lines.append("• (none)")

    return "\n".join(lines)


def maybe_eod_summary(
    option_positions: list[dict],
    equity_orders: list[dict],
    option_orders: list[dict],
    sent: set[str],
    now: datetime | None = None,
) -> tuple[str | None, set[str]]:
    """Return ``(telegram_text, updated_sent)`` when today's EOD slot is due.

    Always sends all three sections when the slot fires, even if every count is
    zero (broker returned empty lists).
    """
    now = (now or datetime.now(_ET)).astimezone(_ET)
    if now.weekday() >= 5:
        return None, sent
    eod = _eod_time()
    if now.time() < eod:
        return None, sent
    slot = f"{now.date().isoformat()}@eod"
    if slot in sent:
        return None, sent
    msg = build_eod_message(
        option_positions=option_positions,
        equity_orders=equity_orders,
        option_orders=option_orders,
        as_of=now.date(),
    )
    return msg, sent | {slot}
