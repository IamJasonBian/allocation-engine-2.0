"""Telegram notifications via bot API.

Module name and `notify(message)` signature preserved for import-stability with
existing call sites in app.brokers.robinhood_client and app.background.
"""

import hashlib
import logging
import os
import threading
import time

import requests

log = logging.getLogger(__name__)

_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")
_DEBOUNCE_WINDOW_SEC = int(os.getenv("ALERT_DEBOUNCE_SECONDS", "300"))
_ALERTS_ENABLED = os.getenv("ALERTS_ENABLED", "false").strip().lower() in (
    "true", "1", "yes", "on",
)

_lock = threading.Lock()
_last_sent: dict[str, tuple[float, int]] = {}
_last_disabled_log_ts: float = 0.0
_DISABLED_LOG_THROTTLE_SEC = 60.0


def _strip_slack_markup(text: str) -> str:
    """Drop Slack-only tokens that would render as literal text in Telegram."""
    return (
        text
        .replace("<!channel>", "")
        .replace("<!here>", "")
        .strip()
    )


def _send(text: str) -> bool:
    if not _TOKEN or not _CHAT_ID:
        log.debug("[telegram] TELEGRAM_BOT_TOKEN/CHAT_ID not set, skipping")
        return False
    try:
        resp = requests.post(
            f"https://api.telegram.org/bot{_TOKEN}/sendMessage",
            json={
                "chat_id": _CHAT_ID,
                "text": text,
                "disable_web_page_preview": True,
            },
            timeout=10,
        )
        resp.raise_for_status()
        log.info("[telegram] Notification sent")
        return True
    except Exception:
        log.exception("[telegram] Failed to send message")
        return False


def notify(message: str, *, bypass_debounce: bool = False):
    """Post a message to the configured Telegram chat.

    Identical messages within ALERT_DEBOUNCE_SECONDS (default 300) are suppressed
    and counted; the next emit appends `[+N suppressed in last Ns]`.
    Pass `bypass_debounce=True` for critical alerts that must always go through.
    """
    if not _ALERTS_ENABLED:
        global _last_disabled_log_ts
        now = time.time()
        if now - _last_disabled_log_ts >= _DISABLED_LOG_THROTTLE_SEC:
            log.info(
                "[telegram] notify() suppressed — ALERTS_ENABLED is not truthy "
                "(set ALERTS_ENABLED=true on Render to enable)"
            )
            _last_disabled_log_ts = now
        return

    text = _strip_slack_markup(message)
    if not text:
        return

    if bypass_debounce or _DEBOUNCE_WINDOW_SEC <= 0:
        _send(text)
        return

    key = hashlib.sha256(text.encode("utf-8")).hexdigest()
    now = time.time()
    with _lock:
        last_ts, suppressed = _last_sent.get(key, (0.0, 0))
        if now - last_ts < _DEBOUNCE_WINDOW_SEC:
            _last_sent[key] = (last_ts, suppressed + 1)
            return
        if suppressed > 0:
            text = f"{text}\n[+{suppressed} suppressed in last {_DEBOUNCE_WINDOW_SEC}s]"
        _last_sent[key] = (now, 0)

    _send(text)
