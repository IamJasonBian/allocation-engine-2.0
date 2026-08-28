#!/usr/bin/env python3
"""Summarize GitHub Actions burn for this repo.

Uses the `gh` CLI (already configured for Render ops). Estimates billable
minutes from job duration; GitHub bills per-minute for private repos.

Examples:
  python scripts/gh_actions_cost.py
  python scripts/gh_actions_cost.py --workflow unload-options-chain.yml --days 7
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from collections import defaultdict
from datetime import datetime, timedelta, timezone


def _gh(*args: str) -> object:
    cmd = ["gh", "api", *args]
    try:
        out = subprocess.check_output(cmd, text=True)
    except subprocess.CalledProcessError as exc:
        print(exc.stderr or exc, file=sys.stderr)
        sys.exit(1)
    return json.loads(out)


def _runs(repo: str, workflow: str | None, cutoff: datetime, per_page: int = 100) -> list[dict]:
    runs: list[dict] = []
    page = 1
    created = cutoff.strftime("%Y-%m-%dT%H:%M:%SZ")
    while True:
        if workflow:
            base = f"repos/{repo}/actions/workflows/{workflow}/runs"
        else:
            base = f"repos/{repo}/actions/runs"
        path = f"{base}?per_page={per_page}&page={page}&created=%3E%3D{created}"
        batch = _gh(path).get("workflow_runs", [])
        if not batch:
            break
        runs.extend(batch)
        if len(batch) < per_page:
            break
        page += 1
        if page > 10:
            break
    return runs


def _job_seconds(repo: str, run_id: int) -> float:
    jobs = _gh(f"repos/{repo}/actions/runs/{run_id}/jobs").get("jobs", [])
    total = 0.0
    for job in jobs:
        started = job.get("started_at")
        completed = job.get("completed_at")
        if not started or not completed:
            continue
        t0 = datetime.fromisoformat(started.replace("Z", "+00:00"))
        t1 = datetime.fromisoformat(completed.replace("Z", "+00:00"))
        total += max(0.0, (t1 - t0).total_seconds())
    return total


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--repo",
        default="IamJasonBian/allocation-engine-2.0",
        help="owner/repo (default: this project)",
    )
    parser.add_argument(
        "--workflow",
        help="workflow file name, e.g. unload-options-chain.yml",
    )
    parser.add_argument(
        "--days",
        type=int,
        default=30,
        help="only include runs in the last N days (default: 30)",
    )
    parser.add_argument(
        "--sample-jobs",
        type=int,
        default=5,
        help="fetch exact job timing for the N most recent runs (default: 5)",
    )
    args = parser.parse_args()

    cutoff = datetime.now(timezone.utc) - timedelta(days=args.days)
    all_runs = _runs(args.repo, args.workflow, cutoff)

    by_name: dict[str, dict[str, int | float]] = defaultdict(
        lambda: {"runs": 0, "failures": 0, "seconds": 0.0}
    )
    recent_for_timing: list[tuple[str, int]] = []

    for run in all_runs:
        name = run.get("name") or run.get("path") or "unknown"
        bucket = by_name[name]
        bucket["runs"] += 1
        if run.get("conclusion") == "failure":
            bucket["failures"] += 1
        recent_for_timing.append((name, run["id"]))

    # Exact timing for a sample of recent runs (API-heavy; small N only).
    avg_sec: dict[str, float] = {}
    for name, run_id in recent_for_timing[: args.sample_jobs]:
        sec = _job_seconds(args.repo, run_id)
        avg_sec[name] = sec

    default_sec = sum(avg_sec.values()) / len(avg_sec) if avg_sec else 11.0

    print(f"GitHub Actions cost estimate — {args.repo}")
    print(f"Window: last {args.days} days (since {cutoff.date().isoformat()} UTC)")
    if args.workflow:
        print(f"Filter: {args.workflow}")
    print()

    total_runs = 0
    total_failures = 0
    total_min = 0.0

    for name in sorted(by_name):
        b = by_name[name]
        sec = avg_sec.get(name, default_sec)
        minutes = b["runs"] * sec / 60.0
        total_runs += b["runs"]
        total_failures += b["failures"]
        total_min += minutes
        fail_pct = (100.0 * b["failures"] / b["runs"]) if b["runs"] else 0
        print(
            f"  {name}\n"
            f"    runs={b['runs']}  failures={b['failures']} ({fail_pct:.0f}%)  "
            f"est_min={minutes:.1f}  (@{sec:.0f}s/run)"
        )

    print()
    print(f"  TOTAL  runs={total_runs}  failures={total_failures}  est_min≈{total_min:.1f}")
    if total_failures and total_runs == total_failures:
        print("  NOTE: 100% failure rate usually means a zombie workflow (script/path gone).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
