#!/usr/bin/env python3
"""Drain options chain snapshots from Redis → Netlify Blobs.

Redis keys:
  options-chain:history:{SYMBOL}  (RPOP drain)
  options-chain:{SYMBOL}          (latest read)

Env: REDIS_HOST, REDIS_PASSWORD, NETLIFY_API_TOKEN, NETLIFY_SITE_ID,
     OPTIONS_SYMBOLS (default NBIS,AVGO,SPY,IWN,MU)

Emits a ``[cost]`` JSON line and appends to ``GITHUB_STEP_SUMMARY`` when set.
"""

from __future__ import annotations

import json
import os
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone

import redis
import requests

BLOBS_URL = "https://api.netlify.com/api/v1/blobs"
STORE_NAME = "options-chain"
DEFAULT_SYMBOLS = "NBIS,AVGO,SPY,IWN,MU"
# Private-repo Linux minutes ≈ $0.008/min on GitHub's metered tier (reference only).
GH_MINUTE_USD = 0.008


@dataclass
class RunCost:
    started_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    duration_sec: float = 0.0
    symbols: list[str] = field(default_factory=list)
    blobs_written: int = 0
    bytes_uploaded: int = 0
    history_drained: int = 0
    redis_ops: int = 0
    errors: list[str] = field(default_factory=list)

    def finish(self, t0: float) -> None:
        self.duration_sec = round(time.monotonic() - t0, 2)

    @property
    def gh_minutes(self) -> float:
        return round(self.duration_sec / 60.0, 4)

    @property
    def gh_usd_est(self) -> float:
        return round(self.gh_minutes * GH_MINUTE_USD, 6)

    def as_dict(self) -> dict:
        return {
            "started_at": self.started_at,
            "duration_sec": self.duration_sec,
            "gh_minutes_est": self.gh_minutes,
            "gh_usd_est": self.gh_usd_est,
            "symbols": self.symbols,
            "blobs_written": self.blobs_written,
            "bytes_uploaded": self.bytes_uploaded,
            "history_drained": self.history_drained,
            "redis_ops": self.redis_ops,
            "errors": self.errors,
            "github_run_id": os.getenv("GITHUB_RUN_ID"),
            "github_workflow": os.getenv("GITHUB_WORKFLOW"),
        }


def get_redis_client() -> redis.Redis:
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


def get_netlify_config() -> tuple[str, str]:
    token = os.getenv("NETLIFY_API_TOKEN")
    site_id = os.getenv("NETLIFY_SITE_ID")
    if not token or not site_id:
        print("ERROR: NETLIFY_API_TOKEN and NETLIFY_SITE_ID required")
        sys.exit(1)
    return token, site_id


def drain_history(client: redis.Redis, history_key: str, cost: RunCost) -> list[dict]:
    entries: list[dict] = []
    for _ in range(BATCH_SIZE):
        entry = client.rpop(history_key)
        cost.redis_ops += 1
        if entry is None:
            break
        try:
            entries.append(json.loads(entry))
        except json.JSONDecodeError:
            print("  WARNING: skipping malformed history entry")
    return entries


def _read_hash(client: redis.Redis, key: str, cost: RunCost) -> dict:
    cost.redis_ops += 1
    raw = client.hgetall(key)
    result: dict = {}
    for field, value in raw.items():
        try:
            result[field] = json.loads(value)
        except (json.JSONDecodeError, TypeError):
            result[field] = value
    return result


def upload_to_blob(
    token: str, site_id: str, blob_key: str, data: dict, cost: RunCost,
) -> None:
    payload = json.dumps(data)
    url = f"{BLOBS_URL}/{site_id}/{STORE_NAME}/{blob_key}"
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    }
    print(f"  [blob] PUT {STORE_NAME}/{blob_key} ({len(payload)} bytes)")
    resp = requests.put(url, headers=headers, data=payload, timeout=30)
    print(f"  [blob] Response: {resp.status_code} {resp.reason}")
    resp.raise_for_status()
    cost.blobs_written += 1
    cost.bytes_uploaded += len(payload.encode("utf-8"))


def unload_symbol(
    client: redis.Redis,
    token: str,
    site_id: str,
    symbol: str,
    cost: RunCost,
) -> None:
    print(f"\n[unloader] Processing {symbol}")

    history_key = f"options-chain:history:{symbol}"
    entries = drain_history(client, history_key)
    cost.history_drained += len(entries)
    print(f"  Drained {len(entries)} history entries")

    latest_chain = _read_hash(client, f"options-chain:{symbol}", cost)
    num_contracts = len([k for k in latest_chain if k != "_meta"])
    print(f"  Latest chain: {num_contracts} contracts")

    if not entries and not latest_chain:
        print(f"  No data for {symbol}. Skipping.")
        return

    now = datetime.now(timezone.utc)
    blob_key = f"{symbol}/{now.strftime('%Y-%m-%dT%H-%M-%S')}"
    payload = {
        "timestamp": now.isoformat(),
        "underlying": symbol,
        "blob_key": blob_key,
        "latest_chain": latest_chain,
        "history_count": len(entries),
        "history": entries,
    }
    upload_to_blob(token, site_id, blob_key, payload, cost)
    print(f"  Done: {len(entries)} history + {num_contracts} chain")


def write_step_summary(cost: RunCost, job_sec: float | None) -> None:
    path = os.getenv("GITHUB_STEP_SUMMARY")
    if not path:
        return
    lines = [
        "## Options chain unload — cost",
        "",
        "| Metric | Value |",
        "|--------|-------|",
        f"| Script duration | {cost.duration_sec}s |",
        f"| Est. GH billable | {cost.gh_minutes} min (~${cost.gh_usd_est}) |",
    ]
    if job_sec is not None:
        job_min = round(job_sec / 60.0, 4)
        lines.append(f"| Job wall (incl. setup) | {round(job_sec, 1)}s ({job_min} min) |")
    lines.extend([
        f"| Blobs written | {cost.blobs_written} |",
        f"| Bytes uploaded | {cost.bytes_uploaded:,} |",
        f"| History drained | {cost.history_drained} |",
        f"| Redis ops | {cost.redis_ops} |",
        f"| Symbols | {', '.join(cost.symbols) or '—'} |",
    ])
    if cost.errors:
        lines.extend(["", f"**Errors:** {len(cost.errors)} — see logs"])
    with open(path, "a", encoding="utf-8") as fh:
        fh.write("\n".join(lines) + "\n")


def main() -> int:
    t0 = time.monotonic()
    job_t0 = os.getenv("UNLOAD_JOB_START_EPOCH")
    job_sec = None
    if job_t0:
        try:
            job_sec = time.time() - float(job_t0)
        except ValueError:
            pass

    cost = RunCost()
    print(f"[unloader] Options chain blob unloader starting at {cost.started_at}")

    client = get_redis_client()
    token, site_id = get_netlify_config()

    cost.symbols = [
        s.strip()
        for s in os.getenv("OPTIONS_SYMBOLS", DEFAULT_SYMBOLS).split(",")
        if s.strip()
    ]
    print(f"[unloader] Symbols: {cost.symbols}")

    for symbol in cost.symbols:
        try:
            unload_symbol(client, token, site_id, symbol, cost)
        except Exception as e:
            msg = f"{symbol}: {e}"
            cost.errors.append(msg)
            print(f"[unloader] ERROR processing {symbol}: {e}")

    client.close()
    cost.finish(t0)

    print(f"\n[unloader] All done.")
    print(f"[cost] {json.dumps(cost.as_dict(), separators=(',', ':'))}")
    write_step_summary(cost, job_sec)
    return 1 if cost.errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
