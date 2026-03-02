#!/usr/bin/env python3
"""
Market Quotes Blob Unloader

Drains market quote snapshots from the Redis history list (market-quotes:history)
and uploads them to the Netlify Blobs 'market-quotes' store.

Also captures the current latest snapshot from the market-quotes hash.

Env vars required:
  REDIS_HOST (default: redis-17054.c99.us-east-1-4.ec2.cloud.redislabs.com:17054)
  REDIS_PASSWORD
  NETLIFY_API_TOKEN
  NETLIFY_SITE_ID
"""

import json
import os
import sys
from datetime import datetime, timezone

import redis
import requests

BLOBS_URL = "https://api.netlify.com/api/v1/blobs"
STORE_NAME = "market-quotes"
HASH_KEY = "market-quotes"
HISTORY_KEY = "market-quotes:history"
BATCH_SIZE = 500


def get_redis_client():
    """Connect to Redis following the same pattern as redis_store.py."""
    host = os.getenv(
        "REDIS_HOST",
        "redis-17054.c99.us-east-1-4.ec2.cloud.redislabs.com:17054",
    )
    password = os.getenv("REDIS_PASSWORD")
    port = 17054
    if ":" in host:
        host, port_str = host.rsplit(":", 1)
        try:
            port = int(port_str)
        except ValueError:
            pass
    try:
        return redis.Redis(
            host=host, port=port, password=password,
            decode_responses=True,
        )
    except Exception as e:
        print(f"ERROR: Redis connection failed: {e}")
        sys.exit(1)


def get_netlify_config():
    """Read Netlify config from env vars."""
    token = os.getenv("NETLIFY_API_TOKEN")
    site_id = os.getenv("NETLIFY_SITE_ID")
    if not token or not site_id:
        print("ERROR: NETLIFY_API_TOKEN and NETLIFY_SITE_ID required")
        sys.exit(1)
    return token, site_id


def drain_history(client, max_entries=BATCH_SIZE):
    """RPOP entries from the history list (oldest first)."""
    entries = []
    for _ in range(max_entries):
        entry = client.rpop(HISTORY_KEY)
        if entry is None:
            break
        try:
            entries.append(json.loads(entry))
        except json.JSONDecodeError:
            print("  WARNING: skipping malformed history entry")
    return entries


def get_latest_quotes(client):
    """Read the current market-quotes hash (latest snapshot per symbol)."""
    raw = client.hgetall(HASH_KEY)
    result = {}
    for key, value in raw.items():
        try:
            result[key] = json.loads(value)
        except (json.JSONDecodeError, TypeError):
            result[key] = value
    return result


def upload_to_blob(token, site_id, blob_key, data):
    """Upload JSON data to Netlify Blobs (matches blob_logger.py pattern)."""
    payload = json.dumps(data)
    url = f"{BLOBS_URL}/{site_id}/{STORE_NAME}/{blob_key}"
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    }
    print(f"  [blob] PUT {STORE_NAME}/{blob_key}")
    print(f"  [blob] Payload size: {len(payload)} bytes")
    resp = requests.put(url, headers=headers, data=payload, timeout=15)
    print(f"  [blob] Response: {resp.status_code} {resp.reason}")
    resp.raise_for_status()


def main():
    print(f"[unloader] Market quotes blob unloader starting at {datetime.now(timezone.utc).isoformat()}")

    client = get_redis_client()
    token, site_id = get_netlify_config()

    # 1. Drain history entries (oldest first via RPOP)
    entries = drain_history(client)
    print(f"[unloader] Drained {len(entries)} history entries from Redis")

    # 2. Read current latest snapshot
    latest = get_latest_quotes(client)
    num_symbols = len([k for k in latest if k != "_meta"])
    print(f"[unloader] Latest quotes: {num_symbols} symbols")

    if not entries and not latest:
        print("[unloader] No market quote data to upload. Done.")
        client.close()
        return

    # 3. Build blob payload
    blob_key = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H-%M-%S")
    payload = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "blob_key": blob_key,
        "latest_quotes": latest,
        "history_count": len(entries),
        "history": entries,
    }

    # 4. Upload to Netlify Blobs
    upload_to_blob(token, site_id, blob_key, payload)
    print(f"[unloader] Done: {len(entries)} history entries + {num_symbols} latest quotes "
          f"-> {STORE_NAME}/{blob_key}")

    client.close()


if __name__ == "__main__":
    main()
