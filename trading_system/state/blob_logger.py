"""
State Logger
Serializes StateManager state and writes it to Netlify Blobs (live mode)
or to a local JSON file (dry-run mode).

Live mode requires NETLIFY_API_TOKEN and NETLIFY_SITE_ID environment variables.
"""

import json
import os
from datetime import datetime
from pathlib import Path

import requests


NETLIFY_BLOBS_URL = "https://api.netlify.com/api/v1/blobs"
STORE_NAME = "order-book"
LOCAL_LOG_DIR = Path(__file__).resolve().parent.parent.parent / "state_logs"


def _get_config():
    """Get Netlify config from environment variables."""
    token = os.getenv("NETLIFY_API_TOKEN")
    site_id = os.getenv("NETLIFY_SITE_ID")
    if not token or not site_id:
        return None
    return {"token": token, "site_id": site_id}


def _serialize_state(state_manager, order_book=None, portfolio=None,
                     drift_metrics=None) -> dict:
    """Serialize StateManager state to a JSON-safe dictionary."""
    snapshot = {
        "timestamp": datetime.now().isoformat(),
        "state": state_manager.state,
        "tickers": {},
    }
    for symbol, ticker in state_manager.tickers.items():
        snapshot["tickers"][symbol] = {
            "orders": [order.get_state() for order in ticker.orders],
            "signal_orders": [order.get_state() for order in ticker.get_signal_orders()],
        }
    if order_book is not None:
        snapshot["order_book"] = order_book
    if portfolio is not None:
        snapshot["portfolio"] = portfolio
        # Include options from the portfolio if present
        if isinstance(portfolio, dict) and portfolio.get("options"):
            snapshot["options"] = portfolio["options"]
    if drift_metrics is not None:
        snapshot["drift_metrics"] = drift_metrics
    return snapshot


def _serialize_value(obj):
    """JSON serializer for objects not serializable by default."""
    if hasattr(obj, "value"):
        return obj.value
    raise TypeError(f"Object of type {type(obj)} is not JSON serializable")


def _log_local(state_manager, order_book=None, portfolio=None,
               drift_metrics=None):
    """Write state snapshot to a local JSON file under state_logs/."""
    LOCAL_LOG_DIR.mkdir(exist_ok=True)
    snapshot = _serialize_state(state_manager, order_book=order_book,
                                portfolio=portfolio, drift_metrics=drift_metrics)
    blob_key = datetime.now().strftime("%Y-%m-%dT%H-%M-%S")
    payload = json.dumps(snapshot, default=_serialize_value, indent=2)

    out_path = LOCAL_LOG_DIR / f"{blob_key}.json"
    out_path.write_text(payload)
    print(f"State logged locally: {out_path}")
    return str(out_path)


def _log_remote(state_manager, order_book=None, portfolio=None,
                drift_metrics=None):
    """Upload state snapshot to Netlify Blobs."""
    config = _get_config()
    if not config:
        print("Netlify Blobs logging skipped: "
              "NETLIFY_API_TOKEN or NETLIFY_SITE_ID not set")
        return None

    snapshot = _serialize_state(state_manager, order_book=order_book,
                                portfolio=portfolio, drift_metrics=drift_metrics)
    blob_key = datetime.now().strftime("%Y-%m-%dT%H-%M-%S")
    payload = json.dumps(snapshot, default=_serialize_value)

    url = f"{NETLIFY_BLOBS_URL}/{config['site_id']}/{STORE_NAME}/{blob_key}"
    headers = {
        "Authorization": f"Bearer {config['token']}",
        "Content-Type": "application/json",
    }

    try:
        resp = requests.put(url, headers=headers, data=payload, timeout=10)
        resp.raise_for_status()
        print(f"State logged to Netlify Blobs: {STORE_NAME}/{blob_key}")
        return blob_key
    except requests.RequestException as e:
        print(f"Failed to log state to Netlify Blobs: {e}")
        return None


def log_state_to_blob(state_manager, live=False, order_book=None,
                      portfolio=None, drift_metrics=None):
    """Log StateManager state. Writes locally in dry-run, uploads to Netlify Blobs when live."""
    if live:
        return _log_remote(state_manager, order_book=order_book,
                           portfolio=portfolio, drift_metrics=drift_metrics)
    return _log_local(state_manager, order_book=order_book,
                      portfolio=portfolio, drift_metrics=drift_metrics)
