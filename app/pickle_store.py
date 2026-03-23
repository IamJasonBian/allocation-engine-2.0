"""Netlify Blobs persistence for the Robinhood session pickle file.

Stores/retrieves the robin_stocks pickle (containing access_token,
refresh_token, and device_token) so it survives Render's ephemeral
filesystem across deploys.
"""

import logging
import os
import pickle

import requests

log = logging.getLogger(__name__)

BLOBS_URL = "https://api.netlify.com/api/v1/blobs"
STORE_NAME = "rh-session"
BLOB_KEY = "robinhood-pickle"

_REQUIRED_PICKLE_KEYS = {"access_token", "token_type", "refresh_token", "device_token"}


def _blob_url() -> str | None:
    """Build the full Netlify Blobs URL, or None if env vars are missing."""
    token = os.getenv("NETLIFY_API_TOKEN")
    site_id = os.getenv("NETLIFY_SITE_ID")
    if not token or not site_id:
        return None
    return f"{BLOBS_URL}/{site_id}/{STORE_NAME}/{BLOB_KEY}"


def _auth_headers() -> dict:
    return {"Authorization": f"Bearer {os.getenv('NETLIFY_API_TOKEN')}"}


def download_pickle(local_path: str) -> bool:
    """Download the session pickle from Netlify Blobs to local_path.

    Validates that the blob is a real pickle with the expected keys
    before writing to disk.  Returns True if a valid pickle was
    restored, False otherwise.
    """
    url = _blob_url()
    if not url:
        log.debug("[pickle_store] Netlify env vars not set, skipping download")
        return False

    try:
        resp = requests.get(url, headers=_auth_headers(), timeout=(5, 10))
        if resp.status_code == 404:
            log.info("[pickle_store] No pickle found in blob store (first deploy?)")
            return False
        resp.raise_for_status()
    except Exception:
        log.exception("[pickle_store] Failed to download pickle from blob store")
        return False

    # Validate contents before writing to disk
    try:
        data = pickle.loads(resp.content)
        missing = _REQUIRED_PICKLE_KEYS - set(data.keys())
        if missing:
            log.warning("[pickle_store] Downloaded pickle missing keys: %s", missing)
            return False
    except Exception:
        log.exception("[pickle_store] Downloaded blob is not a valid pickle")
        return False

    os.makedirs(os.path.dirname(local_path), exist_ok=True)
    with open(local_path, "wb") as f:
        f.write(resp.content)

    log.info("[pickle_store] Restored pickle to %s (%d bytes)", local_path, len(resp.content))
    return True


def upload_pickle(local_path: str) -> bool:
    """Upload the session pickle from local_path to Netlify Blobs.

    Returns True on success, False otherwise.
    """
    url = _blob_url()
    if not url:
        log.debug("[pickle_store] Netlify env vars not set, skipping upload")
        return False

    if not os.path.isfile(local_path):
        log.warning("[pickle_store] No pickle file at %s to upload", local_path)
        return False

    try:
        with open(local_path, "rb") as f:
            payload = f.read()

        headers = _auth_headers()
        headers["Content-Type"] = "application/octet-stream"

        resp = requests.put(url, headers=headers, data=payload, timeout=15)
        resp.raise_for_status()
        log.info("[pickle_store] Uploaded pickle to blob store (%d bytes)", len(payload))
        return True
    except Exception:
        log.exception("[pickle_store] Failed to upload pickle to blob store")
        return False
