#!/usr/bin/env bash
#
# local_resource_audit.sh — audit local machine resource usage and surface the
# usual culprits behind a runaway load average on this dev box.
#
# Read-only by default: it diagnoses and prints the commands to reduce usage,
# but does NOT stop anything unless you pass --reduce.
#
# Background: the single biggest local CPU hog here is the Colima VM, which can
# pin a full core even when its containers are idle (Virtualization.framework
# idle-spin). A misconfigured container restart policy (restart=unless-stopped
# on a job that exits after each run) compounds it into a restart storm that
# drives load average into the 20s. This script finds both, plus forgotten
# dev-server processes still holding RAM and ports.
#
# Usage:
#   scripts/local_resource_audit.sh            # audit only (safe, read-only)
#   scripts/local_resource_audit.sh --reduce   # also stop Colima + restart-looping containers
#
# No 'set -e'/'pipefail': this is a best-effort audit — a closed pipe (e.g. a
# downstream 'head') or a missing tool should not abort the whole report.
set -u

REDUCE=0
[[ "${1:-}" == "--reduce" ]] && REDUCE=1

# RestartCount above this flags a container as a likely restart-loop.
RESTART_LOOP_THRESHOLD=50

hr() { printf '%s\n' "------------------------------------------------------------"; }

echo "=== Load average & memory pressure ==="
uptime
if command -v memory_pressure >/dev/null 2>&1; then
  memory_pressure 2>/dev/null | grep -i "System-wide memory free percentage" || true
fi
hr

echo "=== Top 10 processes by CPU ==="
ps -arcwwwxo pid,pcpu,pmem,etime,comm | head -11
hr

echo "=== Colima / VM ==="
if pgrep -f "Virtualization.framework.*VirtualMachine" >/dev/null 2>&1; then
  vm_pid=$(pgrep -f "Virtualization.framework.*VirtualMachine" | head -1)
  vm_cpu=$(ps -p "$vm_pid" -o pcpu= | tr -d ' ')
  echo "Colima VM running (pid $vm_pid, ${vm_cpu}% CPU)."
  if command -v docker >/dev/null 2>&1 && docker info >/dev/null 2>&1; then
    docker stats --no-stream --format '{{.Name}}\t{{.CPUPerc}}\t{{.MemUsage}}' || true
  fi
  echo "  -> If you are not actively using the stack: colima stop"
  if [[ "$REDUCE" == "1" ]]; then
    echo "  --reduce: stopping Colima..."
    colima stop
  fi
else
  echo "Colima VM not running."
fi
hr

echo "=== Docker containers (restart-loop check) ==="
if command -v docker >/dev/null 2>&1 && docker info >/dev/null 2>&1; then
  printf '%-28s %-22s %s\n' "NAME" "STATUS" "RESTARTS"
  while IFS=$'\t' read -r name status; do
    rc=$(docker inspect "$name" --format '{{.RestartCount}}' 2>/dev/null || echo "?")
    flag=""
    if [[ "$rc" =~ ^[0-9]+$ ]] && (( rc > RESTART_LOOP_THRESHOLD )); then
      flag="  <-- RESTART LOOP"
    fi
    printf '%-28s %-22s %s%s\n' "$name" "$status" "$rc" "$flag"
    if [[ -n "$flag" && "$REDUCE" == "1" ]]; then
      echo "  --reduce: stopping $name (restart loop)..."
      docker stop "$name" >/dev/null
    fi
  done < <(docker ps --format '{{.Names}}\t{{.Status}}')
  echo "  Tip: a container that exits 0 each run under restart=unless-stopped will loop forever."
  echo "       Fix the restart policy instead of relying on 'docker stop'."
else
  echo "Docker not reachable (Colima stopped?) — skipping."
fi
hr

echo "=== Local dev-server listeners (TCP LISTEN) ==="
echo "Forgotten dev servers hold RAM and ports. Review uptime; kill stale ones."
printf '%-7s %-6s %-12s %s\n' "PID" "PORT" "UPTIME" "COMMAND"
lsof -nP -iTCP -sTCP:LISTEN 2>/dev/null \
  | awk 'NR>1 && /python|Python|node|uvicorn/ {print $2, $9}' \
  | sort -u \
  | while read -r pid addr; do
      port="${addr##*:}"
      etime=$(ps -p "$pid" -o etime= 2>/dev/null | tr -d ' ')
      cmd=$(ps -p "$pid" -o command= 2>/dev/null | cut -c1-70)
      [[ -n "$etime" ]] && printf '%-7s %-6s %-12s %s\n' "$pid" "$port" "$etime" "$cmd"
    done
echo "  To kill a stale server: kill <PID>   (re-run its launch command to bring it back)"
hr

echo "Done. Re-run without --reduce any time to re-audit."
