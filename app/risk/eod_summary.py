"""Daily pending option order Telegram summaries (start + end of trading day).

Slots (America/New_York, weekdays):
  - SOD: ``SOD_PENDING_ET`` default 09:35
  - EOD: ``EOD_PENDING_ET`` default 16:05

Each slot fires once per day when the worker polls at or after that time.
Intraday 🐧 drawdown alerts are separate and are **not** gated on these times.

On the first successful tick after process start, sends one ``Startup`` pending
snapshot (deploy / restart check).
"""

from __future__ import annotations

import os
from datetime import date, datetime, time as dtime
from zoneinfo import ZoneInfo

from app.enums import OPEN_STATES

_ET = ZoneInfo("America/New_York")
_OPEN = {s.value for s in OPEN_STATES}


def _parse_hm(raw: str, default: dtime) -> dtime:
    try:
        hour, minute = (int(x) for x in raw.split(":", 1))
        return dtime(hour, minute)
    except (ValueError, TypeError):
        return default


def _day_slots() -> tuple[tuple[dtime, str], ...]:
    sod = _parse_hm(os.getenv("SOD_PENDING_ET", "09:35"), dtime(9, 35))
    eod = _parse_hm(os.getenv("EOD_PENDING_ET", "16:05"), dtime(16, 5))
    return (sod, "SOD"), (eod, "EOD")


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


def build_pending_message(*, option_orders: list[dict], label: str, as_of: date) -> str:
    pending = _pending_option_orders(option_orders)
    lines = [f"📋 {label} pending options ({as_of.isoformat()})"]
    if pending:
        lines.extend(_fmt_pending(o) for o in pending)
    else:
        lines.append("• (none)")
    return "\n".join(lines)


def pending_summaries_due(
    option_orders: list[dict],
    sent: set[str],
    *,
    now: datetime | None = None,
    include_startup: bool = False,
) -> tuple[list[str], set[str]]:
    """Return messages to send this tick and the updated dedupe set."""
    now = (now or datetime.now(_ET)).astimezone(_ET)
    messages: list[str] = []
    new_sent = set(sent)

    if include_startup:
        key = f"{now.date().isoformat()}@startup-pending"
        if key not in new_sent:
            messages.append(build_pending_message(
                option_orders=option_orders, label="Startup", as_of=now.date(),
            ))
            new_sent.add(key)

    if now.weekday() < 5:
        for slot_time, label in _day_slots():
            if now.time() < slot_time:
                continue
            key = f"{now.date().isoformat()}@{label.lower()}-pending"
            if key in new_sent:
                continue
            messages.append(build_pending_message(
                option_orders=option_orders, label=label, as_of=now.date(),
            ))
            new_sent.add(key)

    return messages, new_sent


# Back-compat for tests
def build_eod_pending_message(*, option_orders: list[dict], as_of: date) -> str:
    return build_pending_message(option_orders=option_orders, label="EOD", as_of=as_of)


def maybe_eod_pending_summary(
    option_orders: list[dict],
    sent: set[str],
    now: datetime | None = None,
) -> tuple[str | None, set[str]]:
    msgs, new_sent = pending_summaries_due(option_orders, sent, now=now, include_startup=False)
    return (msgs[0] if msgs else None), new_sent
