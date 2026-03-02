"""Slack notifications via incoming webhook."""

import logging
import os

import requests

log = logging.getLogger(__name__)

WEBHOOK_URL = os.getenv("SLACK_WEBHOOK_URL", "")


def notify(message: str):
    """Post a message to the configured Slack channel.

    Silently skips if SLACK_WEBHOOK_URL is not set.
    """
    if not WEBHOOK_URL:
        log.debug("[slack] SLACK_WEBHOOK_URL not set, skipping")
        return

    try:
        resp = requests.post(
            WEBHOOK_URL,
            json={"text": message},
            timeout=10,
        )
        resp.raise_for_status()
        log.info("[slack] Notification sent")
    except Exception:
        log.exception("[slack] Failed to send notification")
