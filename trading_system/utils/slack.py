"""
Slack Notification Utility
Sends alerts to a Slack channel via incoming webhook.

Requires SLACK_WEBHOOK_URL environment variable.
"""

import os

import requests


def _get_webhook_url():
    """Get Slack webhook URL from environment."""
    return os.getenv("SLACK_WEBHOOK_URL")


def send_slack_alert(message, emoji=":warning:"):
    """Post an alert message to Slack via webhook.

    Args:
        message: The alert text to send.
        emoji: Slack emoji for the bot icon (default :warning:).

    Returns:
        True if the message was sent successfully, False otherwise.
    """
    url = _get_webhook_url()
    if not url:
        print("Slack alert skipped: SLACK_WEBHOOK_URL not set")
        return False

    payload = {
        "text": message,
        "icon_emoji": emoji,
    }

    try:
        resp = requests.post(url, json=payload, timeout=10)
        resp.raise_for_status()
        return True
    except requests.RequestException as e:
        print(f"Failed to send Slack alert: {e}")
        return False
