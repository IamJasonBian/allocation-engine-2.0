"""Snapshot logger — writes engine state to Netlify Blobs with gamma source tag.

Uploads to the same "order-book" store the runtime-service reads from,
so snapshots from this engine appear on the dashboard immediately.
"""

import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path

import requests

NETLIFY_BLOBS_URL = "https://api.netlify.com/api/v1/blobs"
STORE_NAME = "order-book"
LOCAL_LOG_DIR = Path(__file__).resolve().parent / "state_logs"

log = logging.getLogger(__name__)


def _get_config():
    token = os.getenv("NETLIFY_API_TOKEN")
    site_id = os.getenv("NETLIFY_SITE_ID")
    if not token or not site_id:
        return None
    return {"token": token, "site_id": site_id}


def build_snapshot(
    *,
    desired_orders: list[dict],
    alpaca_orders: list[dict],
    positions: list[dict],
    account: dict,
    oto_pairs: list[dict] | None = None,
    drift_metrics: dict | None = None,
    execution_log: list[dict] | None = None,
) -> dict:
    """Build a snapshot dict compatible with the runtime-service schema."""
    now = datetime.now(timezone.utc)
    tickers: dict[str, dict] = {}
    for o in desired_orders:
        sym = o.get("symbol", "UNKNOWN")
        tickers.setdefault(sym, {"orders": [], "signal_orders": []})
        tickers[sym]["orders"].append(o)

    return {
        "timestamp": now.isoformat(),
        "source": "gamma",
        "state": {
            "engine": "allocation-engine-2.0",
            "source": "gamma",
        },
        "tickers": tickers,
        "order_book": alpaca_orders,
        "portfolio": {
            "cash": account.get("cash"),
            "equity": account.get("equity"),
            "buying_power": account.get("buying_power"),
            "positions": positions,
        },
        "oto_pairs": oto_pairs or [],
        "drift_metrics": drift_metrics or {},
        "execution_log": execution_log or [],
    }


def upload_snapshot(snapshot: dict) -> str | None:
    """Upload snapshot to Netlify Blobs. Returns blob key on success."""
    config = _get_config()
    if not config:
        log.warning("Blob upload skipped: NETLIFY_API_TOKEN or NETLIFY_SITE_ID not set")
        return _log_local(snapshot)

    blob_key = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H-%M-%S")
    payload = json.dumps(snapshot, default=str)

    url = f"{NETLIFY_BLOBS_URL}/{config['site_id']}/{STORE_NAME}/{blob_key}"
    headers = {
        "Authorization": f"Bearer {config['token']}",
        "Content-Type": "application/json",
    }

    try:
        resp = requests.put(url, headers=headers, data=payload, timeout=10)
        resp.raise_for_status()
        log.info("Snapshot uploaded: %s/%s (source=gamma)", STORE_NAME, blob_key)
        return blob_key
    except requests.RequestException as e:
        log.error("Blob upload failed: %s", e)
        return _log_local(snapshot)


def _log_local(snapshot: dict) -> str:
    """Fallback: write snapshot to local file."""
    LOCAL_LOG_DIR.mkdir(exist_ok=True)
    blob_key = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H-%M-%S")
    path = LOCAL_LOG_DIR / f"{blob_key}.json"
    path.write_text(json.dumps(snapshot, default=str, indent=2))
    log.info("Snapshot saved locally: %s", path)
    return blob_key
