#!/usr/bin/env python3
"""
Options Chain Blob Unloader

Drains options chain history snapshots from Redis and uploads them to
Netlify Blobs. Handles both chain snapshots and minute bar data for
each configured underlying symbol (IWN, CRWD, etc.).

Redis keys consumed:
  options-chain:history:{SYMBOL}  (RPOP drain)
  options-chain:{SYMBOL}          (latest read)
  options-bars:{SYMBOL}           (latest read)

Env vars required:
  REDIS_HOST (default: redis-17054.c99.us-east-1-4.ec2.cloud.redislabs.com:17054)
  REDIS_PASSWORD
  NETLIFY_API_TOKEN
  NETLIFY_SITE_ID
  OPTIONS_SYMBOLS (default: IWN,CRWD)
"""

import json
import os
import sys
from datetime import datetime, timezone

import redis
import requests

BLOBS_URL = "https://api.netlify.com/api/v1/blobs"
STORE_NAME = "options-chain"
BATCH_SIZE = 500


def get_redis_client():
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
    token = os.getenv("NETLIFY_API_TOKEN")
    site_id = os.getenv("NETLIFY_SITE_ID")
    if not token or not site_id:
        print("ERROR: NETLIFY_API_TOKEN and NETLIFY_SITE_ID required")
        sys.exit(1)
    return token, site_id


def drain_history(client, history_key, max_entries=BATCH_SIZE):
    """RPOP entries from the history list (oldest first)."""
    entries = []
    for _ in range(max_entries):
        entry = client.rpop(history_key)
        if entry is None:
            break
        try:
            entries.append(json.loads(entry))
        except json.JSONDecodeError:
            print("  WARNING: skipping malformed history entry")
    return entries


def get_latest_chain(client, symbol):
    """Read the current options-chain:{symbol} hash."""
    raw = client.hgetall(f"options-chain:{symbol}")
    result = {}
    for key, value in raw.items():
        try:
            result[key] = json.loads(value)
        except (json.JSONDecodeError, TypeError):
            result[key] = value
    return result


def get_latest_bars(client, symbol):
    """Read the current options-bars:{symbol} hash."""
    raw = client.hgetall(f"options-bars:{symbol}")
    result = {}
    for key, value in raw.items():
        try:
            result[key] = json.loads(value)
        except (json.JSONDecodeError, TypeError):
            result[key] = value
    return result


def upload_to_blob(token, site_id, blob_key, data):
    payload = json.dumps(data)
    url = f"{BLOBS_URL}/{site_id}/{STORE_NAME}/{blob_key}"
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    }
    print(f"  [blob] PUT {STORE_NAME}/{blob_key}")
    print(f"  [blob] Payload size: {len(payload)} bytes")
    resp = requests.put(url, headers=headers, data=payload, timeout=30)
    print(f"  [blob] Response: {resp.status_code} {resp.reason}")
    resp.raise_for_status()


def unload_symbol(client, token, site_id, symbol):
    """Drain history and upload chain + bars for one underlying symbol."""
    print(f"\n[unloader] Processing {symbol}")

    # 1. Drain chain history
    history_key = f"options-chain:history:{symbol}"
    entries = drain_history(client, history_key)
    print(f"  Drained {len(entries)} history entries")

    # 2. Read latest chain snapshot
    latest_chain = get_latest_chain(client, symbol)
    num_contracts = len([k for k in latest_chain if k != "_meta"])
    print(f"  Latest chain: {num_contracts} contracts")

    # 3. Read latest bars
    latest_bars = get_latest_bars(client, symbol)
    num_bar_contracts = len([k for k in latest_bars if k != "_meta"])
    print(f"  Latest bars: {num_bar_contracts} contracts with bars")

    if not entries and not latest_chain and not latest_bars:
        print(f"  No data for {symbol}. Skipping.")
        return

    # 4. Build blob payload
    now = datetime.now(timezone.utc)
    blob_key = f"{symbol}/{now.strftime('%Y-%m-%dT%H-%M-%S')}"
    payload = {
        "timestamp": now.isoformat(),
        "underlying": symbol,
        "blob_key": blob_key,
        "latest_chain": latest_chain,
        "latest_bars": latest_bars,
        "history_count": len(entries),
        "history": entries,
    }

    # 5. Upload
    upload_to_blob(token, site_id, blob_key, payload)
    print(f"  Done: {len(entries)} history + {num_contracts} chain + {num_bar_contracts} bars")


def main():
    print(f"[unloader] Options chain blob unloader starting at {datetime.now(timezone.utc).isoformat()}")

    client = get_redis_client()
    token, site_id = get_netlify_config()

    symbols = os.getenv("OPTIONS_SYMBOLS", "IWN,CRWD").split(",")
    symbols = [s.strip() for s in symbols if s.strip()]
    print(f"[unloader] Symbols: {symbols}")

    for symbol in symbols:
        try:
            unload_symbol(client, token, site_id, symbol)
        except Exception as e:
            print(f"[unloader] ERROR processing {symbol}: {e}")

    client.close()
    print(f"\n[unloader] All done.")


if __name__ == "__main__":
    main()
