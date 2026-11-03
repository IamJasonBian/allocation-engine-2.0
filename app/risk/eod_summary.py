"""End-of-trading-day Telegram: pending option orders only.

Once per US market day after ``EOD_SUMMARY_ET`` (default 16:05 ET). Always sends
(even when the pending list is empty).
"""

from __future__ import annotations

import os
from datetime import date, datetime, time as dtime
from zoneinfo import ZoneInfo

from app.enums import OPEN_STATES

_ET = ZoneInfo("America/New_York")
_OPEN = {s.value for s in OPEN_STATES}


def _eod_time() -> dtime:
    raw = os.getenv("EOD_SUMMARY_ET", "16:05")
    hour, minute = (int(x) for x in raw.split(":", 1))
    return dtime(hour, minute)


def _pending_option_orders(orders: list[dict]) -> list[dict]:
    out = []
    for o in orders:
        state = (o.get("state") or o.get("status") or "").lower()
        if state not in _OPEN:
            continue
        row = dict(o)
        legs = []
        for leg in row.get("legs") or []:
            lg = dict(leg)
            lg.setdefault("strike", lg.get("strike_price"))
            lg.setdefault("expiration", lg.get("expiration_date"))
            lg.setdefault("chain_symbol", row.get("symbol") or lg.get("chain_symbol"))
            legs.append(lg)
        if legs:
            row["legs"] = legs
        out.append(row)
    return sorted(out, key=lambda x: (x.get("symbol") or "", x.get("created_at") or ""))


def _fmt_pending(o: dict) -> str:
    legs = o.get("legs") or []
    sym = o.get("symbol") or (legs[0].get("chain_symbol") if legs else "?")
    state = o.get("state") or o.get("status") or "?"
    qty = o.get("quantity") or 0
    price = o.get("limit_price")
    price_s = f" @ ${float(price):.2f}" if price else ""
    if len(legs) >= 2:
        strikes = sorted(float(leg.get("strike") or 0) for leg in legs)
        exp = legs[0].get("expiration") or "?"
        strat = o.get("opening_strategy") or o.get("direction") or "spread"
        return f"• {sym} {strat} {strikes[0]:g}/{strikes[1]:g} {exp} x{qty:g}{price_s} ({state})"
    strat = o.get("opening_strategy") or o.get("direction") or "order"
    return f"• {sym} {strat} x{qty:g}{price_s} ({state})"


def build_eod_pending_message(*, option_orders: list[dict], as_of: date) -> str:
    pending = _pending_option_orders(option_orders)
    lines = [f"📋 EOD pending options ({as_of.isoformat()})"]
    if pending:
        lines.extend(_fmt_pending(o) for o in pending)
    else:
        lines.append("• (none)")
    return "\n".join(lines)


def maybe_eod_pending_summary(
    option_orders: list[dict],
    sent: set[str],
    now: datetime | None = None,
) -> tuple[str | None, set[str]]:
    """Return ``(message, updated_sent)`` when today's EOD window is due."""
    now = (now or datetime.now(_ET)).astimezone(_ET)
    if now.weekday() >= 5 or now.time() < _eod_time():
        return None, sent
    slot = f"{now.date().isoformat()}@eod-pending"
    if slot in sent:
        return None, sent
    msg = build_eod_pending_message(option_orders=option_orders, as_of=now.date())
    return msg, sent | {slot}
