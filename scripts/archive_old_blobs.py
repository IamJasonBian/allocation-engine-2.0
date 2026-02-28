#!/usr/bin/env python3
"""
Archive old order-book blobs to state-logs.

Moves blobs older than RETENTION_DAYS from the order-book store
to the state-logs store, then deletes from order-book.

Env vars required:
  NETLIFY_API_TOKEN
  NETLIFY_SITE_ID
"""

import json
import os
import sys
from datetime import datetime, timedelta

import requests

BLOBS_URL = "https://api.netlify.com/api/v1/blobs"
SRC_STORE = "order-book"
DST_STORE = "state-logs"
RETENTION_DAYS = int(os.getenv("RETENTION_DAYS", "7"))


def get_config():
    token = os.getenv("NETLIFY_API_TOKEN")
    site_id = os.getenv("NETLIFY_SITE_ID")
    if not token or not site_id:
        print("ERROR: NETLIFY_API_TOKEN and NETLIFY_SITE_ID required")
        sys.exit(1)
    return token, site_id


def list_blobs(token, site_id, store):
    """List all blob keys in a store."""
    url = f"{BLOBS_URL}/{site_id}/{store}?prefix="
    headers = {"Authorization": f"Bearer {token}"}
    resp = requests.get(url, headers=headers, timeout=15)
    resp.raise_for_status()
    return [b["key"] for b in resp.json().get("blobs", [])]


def get_blob(token, site_id, store, key):
    """Get blob content."""
    url = f"{BLOBS_URL}/{site_id}/{store}/{key}"
    headers = {"Authorization": f"Bearer {token}"}
    resp = requests.get(url, headers=headers, timeout=15)
    resp.raise_for_status()
    return resp.text


def put_blob(token, site_id, store, key, data):
    """Write blob content."""
    url = f"{BLOBS_URL}/{site_id}/{store}/{key}"
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    }
    resp = requests.put(url, headers=headers, data=data, timeout=15)
    resp.raise_for_status()
    return resp.status_code


def delete_blob(token, site_id, store, key):
    """Delete a blob."""
    url = f"{BLOBS_URL}/{site_id}/{store}/{key}"
    headers = {"Authorization": f"Bearer {token}"}
    resp = requests.delete(url, headers=headers, timeout=15)
    resp.raise_for_status()
    return resp.status_code


def main():
    token, site_id = get_config()
    cutoff = (datetime.now() - timedelta(days=RETENTION_DAYS)).strftime("%Y-%m-%dT")

    print(f"Archiving order-book blobs older than {RETENTION_DAYS} days (cutoff: {cutoff})")

    keys = list_blobs(token, site_id, SRC_STORE)
    old_keys = sorted([k for k in keys if k < cutoff])

    print(f"Total blobs in {SRC_STORE}: {len(keys)}")
    print(f"Blobs to archive: {len(old_keys)}")

    if not old_keys:
        print("Nothing to archive.")
        return

    moved = 0
    failed = 0
    for key in old_keys:
        try:
            # Copy to state-logs
            data = get_blob(token, site_id, SRC_STORE, key)
            put_blob(token, site_id, DST_STORE, key, data)

            # Delete from order-book
            delete_blob(token, site_id, SRC_STORE, key)

            moved += 1
            print(f"  archived: {key}")
        except Exception as e:
            failed += 1
            print(f"  FAILED {key}: {e}")

    print(f"\nDone: {moved} archived, {failed} failed")


if __name__ == "__main__":
    main()
