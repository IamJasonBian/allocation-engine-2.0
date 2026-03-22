"""Slack alerting observer — posts DQ events to a Slack webhook."""

from __future__ import annotations

import logging

import requests

from app.risk.events import RiskEvent
from app.risk.observer import RiskObserver

log = logging.getLogger(__name__)

_SEVERITY_EMOJI = {"critical": ":rotating_light:", "warning": ":warning:", "info": ":information_source:"}


class SlackAlertObserver(RiskObserver):
    """Posts risk events to a Slack incoming-webhook URL."""

    def __init__(self, webhook_url: str, timeout: int = 10) -> None:
        self.webhook_url = webhook_url
        self.timeout = timeout

    def update(self, symbol: str, price: float) -> None:
        """Price update — Slack observer only acts on risk events, not raw prices."""
        pass

    def on_risk_event(self, event: RiskEvent) -> None:
        emoji = _SEVERITY_EMOJI.get(event.severity, "")
        text = (
            f"{emoji} *Risk DQ — {event.event_type.value.replace('_', ' ').title()}*\n"
            f">*Symbol:* `{event.symbol}`\n"
            f">*Drift:* {event.drift_pct:.2%}\n"
            f">*Severity:* {event.severity}\n"
            f">*Snapshot:* `{event.snapshot_key or 'n/a'}`\n"
            f">_{event.message}_"
        )
        payload = {"text": text}

        try:
            resp = requests.post(self.webhook_url, json=payload, timeout=self.timeout)
            resp.raise_for_status()
            log.info("Slack alert sent for %s %s", event.symbol, event.event_type)
        except requests.RequestException:
            log.exception("Failed to send Slack alert for %s", event.symbol)
