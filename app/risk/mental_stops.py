"""Alert-only "mental stops" for every open long option position, by P&L alone.

Nothing here places or cancels an order. Each position (a vertical spread's two
legs are combined into one) is alerted once per loss tier it crosses; if it
recovers by more than REARM_BAND it can alert again. The check switches itself
off after OPTION_MENTAL_STOPS_UNTIL (default 2026-11-23, three weeks out).
"""

from __future__ import annotations

import logging
import os
from datetime import date, datetime, time as dtime
from zoneinfo import ZoneInfo

from app.enums import RiskEventType
from app.risk.events import RiskEvent

log = logging.getLogger(__name__)

TIERS = (0.40, 0.60, 0.80)          # loss fractions of cost: -40%, -60%, -80%
REARM_BAND = 0.10                   # must recover this far above a tier to re-arm
DEFAULT_UNTIL = date(2026, 11, 23)


def _until() -> date:
    raw = os.getenv("OPTION_MENTAL_STOPS_UNTIL")
    return date.fromisoformat(raw) if raw else DEFAULT_UNTIL


def _num(v) -> float:
    try:
        return float(v or 0)
    except (TypeError, ValueError):
        return 0.0


def group_positions(options_positions: list[dict]) -> list[dict]:
    """Return trackable units: single longs, and long/short verticals as one.

    Args:
        options_positions: Dicts from ``broker.options_positions()``.

    Returns:
        Dicts with key, label, qty, cost and mark (per share, USD).
    """
    longs = [p for p in options_positions if p.get("position_type") != "short"]
    shorts = [p for p in options_positions if p.get("position_type") == "short"]
    units, used = [], set()
    for lp in longs:
        sym, kind, exp = lp.get("chain_symbol"), lp.get("option_type"), lp.get("expiration")
        qty = _num(lp.get("quantity"))
        match = next((s for s in shorts if id(s) not in used
                      and (s.get("chain_symbol"), s.get("option_type"), s.get("expiration"))
                      == (sym, kind, exp) and _num(s.get("quantity")) == qty), None)
        if match is not None:
            cost = _num(lp.get("purchase_price")) - _num(match.get("purchase_price"))
            mark = _num(lp.get("mark_price")) - _num(match.get("mark_price"))
            used.add(id(match))
            key = f"{sym} {str(kind).upper()} {lp.get('strike')}/{match.get('strike')} {exp} SPREAD"
        else:
            cost, mark = _num(lp.get("purchase_price")), _num(lp.get("mark_price"))
            key = f"{sym} {str(kind).upper()} {lp.get('strike')} {exp}"
        if cost > 0 and lp.get("mark_price") is not None:
            units.append({"key": key, "qty": qty, "cost": cost, "mark": mark})
    return units


def check_mental_stops(options_positions: list[dict], alerted: dict[str, int],
                       today: date | None = None) -> tuple[list[RiskEvent], dict[str, int]]:
    """Return newly crossed P&L-tier events and the updated per-position state.

    Args:
        options_positions: Dicts from ``broker.options_positions()``.
        alerted: Deepest tier index alerted per position key (-1 = none).
        today: Override for the current date (tests).

    Returns:
        ``(events, new_alerted)``; empty and unchanged after the cutoff date.
    """
    if (today or date.today()) > _until():
        return [], alerted
    events, state = [], {}
    for u in group_positions(options_positions):
        loss = (u["cost"] - u["mark"]) / u["cost"]
        prev = alerted.get(u["key"], -1)
        reached = max((i for i, t in enumerate(TIERS) if loss >= t), default=-1)
        while prev >= 0 and loss < TIERS[prev] - REARM_BAND:
            prev -= 1                                   # recovered: re-arm
        if reached > prev:
            usd = (u["mark"] - u["cost"]) * 100 * u["qty"]
            events.append(RiskEvent(
                event_type=RiskEventType.OPTION_MENTAL_STOP, symbol=u["key"],
                drift_pct=max(loss, 0.15 if reached >= 1 else 0.0),
                message=(f"Mental stop {TIERS[reached]:.0%}: {u['key']} x{u['qty']:g} "
                         f"cost ${u['cost']:.2f} -> mark ${u['mark']:.2f} "
                         f"({-loss:+.0%}, ${usd:+,.0f}). Alert only — nothing sold."),
                metadata={"tier": TIERS[reached], "pnl_usd": round(usd, 2)}))
            prev = reached
        state[u["key"]] = prev
    return events, state


DIGEST_TIMES = (dtime(9, 35), dtime(15, 45))     # ET, market days
_ET = ZoneInfo("America/New_York")


def pnl_digest(options_positions: list[dict], sent: set[str],
               now: datetime | None = None) -> tuple[str | None, set[str]]:
    """Build the all-positions P&L message when a digest slot is due.

    Args:
        options_positions: Dicts from ``broker.options_positions()``.
        sent: Slots already sent, as ``"YYYY-MM-DD@HH:MM"`` strings.
        now: Override for the current time (tests).

    Returns:
        ``(message or None, updated sent set)``. None outside market days,
        before a slot, after the mental-stop cutoff, or when already sent.
    """
    now = (now or datetime.now(_ET)).astimezone(_ET)
    if now.weekday() >= 5 or now.date() > _until():
        return None, sent
    due = [t for t in DIGEST_TIMES if now.time() >= t]
    if not due:
        return None, sent
    slot = f"{now.date().isoformat()}@{due[-1].strftime('%H:%M')}"
    if slot in sent:
        return None, sent
    units = sorted(group_positions(options_positions), key=lambda u: u["key"])
    if not units:
        return None, sent | {slot}
    lines, total = [], 0.0
    for u in units:
        loss = (u["cost"] - u["mark"]) / u["cost"]
        usd = (u["mark"] - u["cost"]) * 100 * u["qty"]
        total += usd
        lines.append(f"{u['key']} x{u['qty']:g}: cost ${u['cost']:.2f} -> mark "
                     f"${u['mark']:.2f} ({-loss:+.0%}, ${usd:+,.0f})")
    msg = ("OPTION P&L - mental stops (alert only)\n" + "\n".join(lines)
           + f"\nTotal ${total:+,.0f}")
    return msg, sent | {slot}
