"""Netlify Blobs sync — uploads order book snapshots to the 'order-book' store."""

import json
import logging
import os
from datetime import datetime, timezone

import requests

log = logging.getLogger(__name__)

BLOBS_URL = "https://api.netlify.com/api/v1/blobs"
STORE_NAME = "order-book"


def sync_to_blob(positions, open_orders, account):
    """Upload current portfolio state to Netlify Blobs.

    Writes two keys:
      - 'latest': always-current snapshot for the frontend
      - '{timestamp}': timestamped copy for history
    """
    token = os.getenv("NETLIFY_API_TOKEN")
    site_id = os.getenv("NETLIFY_SITE_ID")
    if not token or not site_id:
        log.debug("[blob] NETLIFY_API_TOKEN or NETLIFY_SITE_ID not set, skipping")
        return

    now = datetime.now(timezone.utc)
    snapshot = {
        "timestamp": now.isoformat(),
        "account": account,
        "positions": positions,
        "open_orders": open_orders,
        "num_positions": len(positions),
        "num_open_orders": len(open_orders),
    }

    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    }
    payload = json.dumps(snapshot)

    for blob_key in ("latest", now.strftime("%Y-%m-%dT%H-%M-%S")):
        url = f"{BLOBS_URL}/{site_id}/{STORE_NAME}/{blob_key}"
        try:
            resp = requests.put(url, headers=headers, data=payload, timeout=15)
            resp.raise_for_status()
            log.info("[blob] PUT %s/%s -> %s (%d bytes)",
                     STORE_NAME, blob_key, resp.status_code, len(payload))
        except Exception:
            log.exception("[blob] Failed to upload %s/%s", STORE_NAME, blob_key)
