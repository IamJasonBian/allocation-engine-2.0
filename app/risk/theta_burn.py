"""Theta burn: how much of an option loss is time decay, and when decay dominates.

Pure functions over ``broker.options_positions()`` dicts. ``greeks.theta`` is
Robinhood's per-share, per-day theta (None when market data had none).
Longs only: theta works for a short. Alert-only; nothing here trades.
"""

from __future__ import annotations

from datetime import date

from app.risk.option_coverage import position_label


def _num(v) -> float:
    try:
        return float(v or 0)
    except (TypeError, ValueError):
        return 0.0


def _is_short(p: dict) -> bool:
    return str(p.get("position_type", "")).lower() == "short"


def _theta(p: dict) -> float | None:
    theta = (p.get("greeks") or {}).get("theta")
    try:
        return None if theta is None else float(theta)
    except (TypeError, ValueError):
        return None


def _shares(p: dict) -> float:
    return _num(p.get("quantity")) * (_num(p.get("multiplier")) or 100.0)


def position_value(p: dict) -> float:
    """Market value of the position in USD (mark × qty × multiplier).

    Args:
        p: One dict from ``broker.options_positions()``.

    Returns:
        The value, 0.0 when the mark is missing.
    """
    return _num(p.get("mark_price")) * _shares(p)


def daily_theta_burn(p: dict) -> float | None:
    """USD the position loses per day to time decay.

    Args:
        p: One dict from ``broker.options_positions()``.

    Returns:
        ``|theta| × qty × multiplier``, or None for shorts and missing theta.
    """
    theta = _theta(p)
    if _is_short(p) or theta is None:
        return None
    return abs(theta) * _shares(p)


def burn_pct_of_value(p: dict) -> float | None:
    """Daily burn as a fraction of the position's current value.

    Args:
        p: One dict from ``broker.options_positions()``.

    Returns:
        ``daily_theta_burn / position_value``, or None when either is unavailable.
    """
    burn, value = daily_theta_burn(p), position_value(p)
    if burn is None or value <= 0:
        return None
    return burn / value


def days_to_zero(p: dict) -> float | None:
    """Days until decay alone would erase the position's value at today's rate.

    Compare with ``p["dte"]``: fewer days to zero than DTE means theta alone
    would wipe the position out before expiry.

    Args:
        p: One dict from ``broker.options_positions()``.

    Returns:
        ``position_value / daily_theta_burn``, or None when burn is missing or zero.
    """
    burn = daily_theta_burn(p)
    if not burn:
        return None
    return position_value(p) / burn


def net_theta_burn(long_leg: dict, short_leg: dict | None = None) -> float | None:
    """USD per day lost to decay by a long, or a long/short vertical as one unit.

    Args:
        long_leg: The long position.
        short_leg: The paired short leg, if any; its decay offsets the long's.

    Returns:
        Net USD per day (negative when decay favours the spread), or None
        when any leg's theta is missing.
    """
    burn = daily_theta_burn(long_leg)
    if burn is None:
        return None
    if short_leg is None:
        return burn
    short_theta = _theta(short_leg)
    if short_theta is None:
        return None
    return burn - abs(short_theta) * _shares(short_leg)


def theta_text(burn: float | None) -> str:
    """``"θ −$Z/day"`` (or ``+$Z`` when decay helps); empty when unknown.

    Args:
        burn: USD per day from ``daily_theta_burn`` / ``net_theta_burn``.

    Returns:
        The formatted note, or ``""`` when ``burn`` is None.
    """
    if burn is None:
        return ""
    return f"θ {'−' if burn >= 0 else '+'}${abs(burn):,.2f}/day"


def book_theta_text(positions: list[dict]) -> str:
    """Total daily burn across longs with theta, for the summary line.

    Args:
        positions: Dicts from ``broker.options_positions()``.

    Returns:
        ``"θ −$Z/day"`` summed over longs with theta, or ``""`` when none have it.
    """
    burns = [b for b in (daily_theta_burn(p) for p in positions or []) if b is not None]
    return theta_text(sum(burns)) if burns else ""


def theta_burn_alerts(positions: list[dict], sent: set[str], *, burn_pct: float,
                      dte_warn: int, today: date | None = None
                      ) -> tuple[list[str], set[str]]:
    """One alert per contract per day for losing longs where decay dominates.

    Fires when the position is at a loss AND (daily burn ≥ ``burn_pct``% of
    value OR DTE ≤ ``dte_warn``).

    Args:
        positions: Dicts from ``broker.options_positions()``.
        sent: ``"<date>|<label>"`` keys already alerted.
        burn_pct: Burn threshold, percent of value per day.
        dte_warn: DTE at or below which a losing long alerts.
        today: Override for the current date (tests).

    Returns:
        ``(messages, new_sent)``; keys from earlier days are dropped.
    """
    day = (today or date.today()).isoformat()
    new_sent = {k for k in sent if k.startswith(f"{day}|")}
    messages = []
    for p in positions or []:
        burn = daily_theta_burn(p)
        cost = _num(p.get("purchase_price")) * _shares(p)
        if burn is None or cost <= 0 or not p.get("expiration"):
            continue
        pnl = position_value(p) - cost
        pct = burn_pct_of_value(p)
        dte = int(_num(p.get("dte")))
        if pnl >= 0 or not ((pct is not None and pct >= burn_pct / 100.0) or dte <= dte_warn):
            continue
        label = position_label(p)
        key = f"{day}|{label}"
        if key in new_sent:
            continue
        pct_s = "n/a" if pct is None else f"{pct:.1%}"
        zero = days_to_zero(p)
        zero_s = f" · zero in ~{zero:.0f}d at this rate" if zero is not None and zero < dte else ""
        messages.append(
            f"🐧 Theta burn: {label} x{_num(p.get('quantity')):g}\n"
            f"P&L ${pnl:+,.0f} ({pnl / cost:+.0%}) · theta −${burn:,.2f}/day ({pct_s} of value)"
            f" · DTE {dte}{zero_s}")
        new_sent.add(key)
    return messages, new_sent
