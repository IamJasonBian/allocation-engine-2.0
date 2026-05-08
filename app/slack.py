"""Slack notifications via incoming webhook.

Standing fallback path — swap this in if Telegram becomes unreliable. Same
public surface as the Telegram version (`notify(message, *, bypass_debounce)`)
plus the same in-process debouncing, so call sites and operator behavior are
unchanged when this PR is merged.
"""

import hashlib
import logging
import os
import threading
import time

import requests

log = logging.getLogger(__name__)

_WEBHOOK_URL = os.getenv("SLACK_WEBHOOK_URL", "")
_DEBOUNCE_WINDOW_SEC = int(os.getenv("ALERT_DEBOUNCE_SECONDS", "300"))

_lock = threading.Lock()
_last_sent: dict[str, tuple[float, int]] = {}


def _send(text: str) -> bool:
    if not _WEBHOOK_URL:
        log.debug("[slack] SLACK_WEBHOOK_URL not set, skipping")
        return False
    try:
        resp = requests.post(_WEBHOOK_URL, json={"text": text}, timeout=10)
        resp.raise_for_status()
        log.info("[slack] Notification sent")
        return True
    except Exception:
        log.exception("[slack] Failed to send message")
        return False


def notify(message: str, *, bypass_debounce: bool = False):
    """Post a message to the configured Slack webhook.

    Identical messages within ALERT_DEBOUNCE_SECONDS (default 300) are suppressed
    and counted; the next emit appends `[+N suppressed in last Ns]`. Slack
    incoming-webhook quotas are tight (HTTP 429 message_limit_exceeded under
    sustained load), so the debouncer matters more here than on Telegram.
    Pass `bypass_debounce=True` for critical alerts that must always go through.
    """
    text = message.strip()
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
