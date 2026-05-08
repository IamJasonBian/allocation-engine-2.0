"""Risk alerting observer — posts DQ events via the Telegram notifier.

Class name `SlackAlertObserver` preserved so existing imports in main.py and
app.background continue to resolve.
"""

from __future__ import annotations

import logging

from app.risk.events import RiskEvent
from app.risk.observer import RiskObserver
from app.slack import notify as _notify

log = logging.getLogger(__name__)

_SEVERITY_PREFIX = {
    "critical": "🚨 CRITICAL",
    "warning": "⚠️ WARNING",
    "info": "ℹ️ INFO",
}


class SlackAlertObserver(RiskObserver):
    """Posts risk events to Telegram (class name preserved for compat)."""

    def __init__(self, webhook_url: str | None = None, timeout: int = 10) -> None:
        # webhook_url retained for backward-compat with existing call sites; ignored.
        self.timeout = timeout

    def update(self, symbol: str, price: float) -> None:
        pass

    def on_risk_event(self, event: RiskEvent) -> None:
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
