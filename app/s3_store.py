"""S3 order-event store — persists every OrderEvent as a JSON-lines file.

Layout:
  s3://{bucket}/{prefix}/events/{date}/orders.jsonl   — append-style daily file
  s3://{bucket}/{prefix}/snapshots/{date}/{HH-MM}.json — periodic full snapshots

Events are buffered in memory and flushed to S3 on each sync call.  Each
flush reads the existing daily file (if any), appends new events, and
re-uploads.  For the snapshot path, we write the full current state so the
API can serve historical lookbacks.
"""

import json
import logging
import os
from datetime import datetime, timezone
from io import BytesIO

log = logging.getLogger(__name__)

_client = None


def _get_client():
    """Lazily create and cache a boto3 S3 client."""
    global _client
    if _client is not None:
        return _client
    try:
        import boto3
        region = os.getenv("AWS_REGION", "us-east-1")
        _client = boto3.client("s3", region_name=region)
        return _client
    except Exception:
        log.warning("[s3] boto3 not available or AWS credentials not configured")
        return None


def _bucket():
    return os.getenv("S3_BUCKET", "")


def _prefix():
    return os.getenv("S3_PREFIX", "order-events")


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def sync_order_events(order_events: list[dict], positions=None,
                      options_positions=None, account=None):
    """Flush order events and a snapshot to S3.

    Args:
        order_events: List of OrderEvent dicts (equity + option).
        positions: Current stock positions (for snapshot).
        options_positions: Current options positions (for snapshot).
        account: Account summary (for snapshot).
    """
    bucket = _bucket()
    if not bucket:
        log.debug("[s3] S3_BUCKET not set, skipping")
        return

    client = _get_client()
    if not client:
        return

    now = datetime.now(timezone.utc)
    date_str = now.strftime("%Y-%m-%d")
    prefix = _prefix()

    # --- 1. Append events to daily JSONL file ---
    if order_events:
        events_key = f"{prefix}/events/{date_str}/orders.jsonl"
        _append_jsonl(client, bucket, events_key, order_events, now)

    # --- 2. Write periodic snapshot ---
    snapshot_key = f"{prefix}/snapshots/{date_str}/{now.strftime('%H-%M')}.json"
    snapshot = {
        "timestamp": now.isoformat(),
        "account": account or {},
        "positions": positions or [],
        "options_positions": options_positions or [],
        "order_events": order_events,
        "num_equity_orders": sum(1 for e in order_events
                                 if e.get("asset_type") == "equity"),
        "num_option_orders": sum(1 for e in order_events
                                 if e.get("asset_type") == "option"),
    }
    _put_json(client, bucket, snapshot_key, snapshot)

    log.info("[s3] Synced %d events to %s, snapshot to %s",
             len(order_events), events_key if order_events else "(none)",
             snapshot_key)


def get_events(date: str | None = None, asset_type: str | None = None,
               limit: int = 500) -> list[dict]:
    """Read order events from S3 for a given date.

    Args:
        date: ISO date string (YYYY-MM-DD). Defaults to today.
        asset_type: Filter by "equity" or "option". None returns both.
        limit: Max events to return.

    Returns:
        List of OrderEvent dicts, newest first.
    """
    bucket = _bucket()
    if not bucket:
        return []

    client = _get_client()
    if not client:
        return []

    if not date:
        date = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    prefix = _prefix()
    events_key = f"{prefix}/events/{date}/orders.jsonl"

    try:
        resp = client.get_object(Bucket=bucket, Key=events_key)
        body = resp["Body"].read().decode("utf-8")
    except client.exceptions.NoSuchKey:
        return []
    except Exception:
        log.exception("[s3] Failed to read %s", events_key)
        return []

    events = []
    for line in body.strip().split("\n"):
        if not line.strip():
            continue
        try:
            evt = json.loads(line)
            if asset_type and evt.get("asset_type") != asset_type:
                continue
            events.append(evt)
        except json.JSONDecodeError:
            continue

    # Newest first, respect limit
    events.reverse()
    return events[:limit]


def list_event_dates(days: int = 30) -> list[str]:
    """List dates that have event files in S3.

    Returns:
        List of date strings (YYYY-MM-DD), newest first.
    """
    bucket = _bucket()
    if not bucket:
        return []

    client = _get_client()
    if not client:
        return []

    prefix = _prefix()
    events_prefix = f"{prefix}/events/"

    try:
        paginator = client.get_paginator("list_objects_v2")
        dates = set()
        for page in paginator.paginate(Bucket=bucket, Prefix=events_prefix,
                                       Delimiter="/"):
            for cp in page.get("CommonPrefixes", []):
                # e.g. "order-events/events/2026-03-18/"
                date_part = cp["Prefix"].rstrip("/").rsplit("/", 1)[-1]
                dates.add(date_part)
        result = sorted(dates, reverse=True)
        return result[:days]
    except Exception:
        log.exception("[s3] Failed to list event dates")
        return []


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _append_jsonl(client, bucket: str, key: str, events: list[dict],
                  now: datetime):
    """Read existing JSONL from S3, append new events, re-upload."""
    existing = ""
    try:
        resp = client.get_object(Bucket=bucket, Key=key)
        existing = resp["Body"].read().decode("utf-8")
    except client.exceptions.NoSuchKey:
        pass
    except Exception:
        log.warning("[s3] Could not read existing %s, starting fresh", key)

    # Stamp each event with sync time
    new_lines = []
    for evt in events:
        row = dict(evt)
        row["_synced_at"] = now.isoformat()
        new_lines.append(json.dumps(row, default=str))

    combined = existing.rstrip("\n")
    if combined:
        combined += "\n"
    combined += "\n".join(new_lines) + "\n"

    try:
        client.put_object(
            Bucket=bucket, Key=key,
            Body=combined.encode("utf-8"),
            ContentType="application/x-ndjson",
        )
    except Exception:
        log.exception("[s3] Failed to write %s", key)


def _put_json(client, bucket: str, key: str, data: dict):
    """Write a JSON object to S3."""
    try:
        client.put_object(
            Bucket=bucket, Key=key,
            Body=json.dumps(data, default=str).encode("utf-8"),
            ContentType="application/json",
        )
    except Exception:
        log.exception("[s3] Failed to write %s", key)
