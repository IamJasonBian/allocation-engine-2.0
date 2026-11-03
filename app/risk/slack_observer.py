"""Risk alerting observer — posts DQ events via the Telegram notifier.

Class name `SlackAlertObserver` preserved so existing imports in main.py and
app.background continue to resolve.
"""

from __future__ import annotations

import logging

from app.risk.events import RiskEvent
from app.risk.observer import RiskObserver
from app.risk.theta_burn import theta_text
from app.slack import notify as _notify

log = logging.getLogger(__name__)

_SEVERITY_PREFIX = {
    "critical": "🚨 CRITICAL",
    "warning": "⚠️ WARNING",
    "info": "ℹ️ INFO",
}


def _option_text(event: RiskEvent) -> str | None:
    """Short 🐧 alert with buy vs current price, for option drawdown/mental-stop events."""
    m = event.metadata or {}
    buy = m.get("purchase_price", m.get("cost"))
    now = m.get("mark_price", m.get("mark"))
    if buy is None or now is None or not float(buy):
        return None
    buy, now, qty = float(buy), float(now), float(m.get("quantity") or 0)
    usd = (now - buy) * 100 * qty
    theta = theta_text(m.get("theta_usd"))
    return (
        f"🐧 {event.symbol} x{qty:g}\n"
        f"Buy ${buy:.2f} → Now ${now:.2f} ({(now - buy) / buy:+.0%}, ${usd:+,.0f})"
        + (f" · {theta}" if theta else "")
    )


class SlackAlertObserver(RiskObserver):
    """Posts risk events to Telegram (class name preserved for compat)."""

    def __init__(self, webhook_url: str | None = None, timeout: int = 10) -> None:
        # webhook_url retained for backward-compat with existing call sites; ignored.
        self.timeout = timeout

    def update(self, symbol: str, price: float) -> None:
        pass

    def on_risk_event(self, event: RiskEvent) -> None:
        option_text = _option_text(event)
        if option_text:
            _notify(option_text, bypass_debounce=True)
            return
        prefix = _SEVERITY_PREFIX.get(event.severity, "")
        text = (
            f"{prefix} Risk DQ — {event.event_type.value.replace('_', ' ').title()}\n"
            f"Symbol: {event.symbol}\n"
            f"Drift: {event.drift_pct:.2%}\n"
            f"Severity: {event.severity}\n"
            f"Snapshot: {event.snapshot_key or 'n/a'}\n"
            f"{event.message}"
        )
        _notify(text, bypass_debounce=(event.severity == "critical"))
