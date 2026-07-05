#!/usr/bin/env bash
#
# Standalone installer for auth-service. Designed for a fresh e2-micro.
#
#   curl -fsSL https://raw.githubusercontent.com/IamJasonBian/allocation-engine-2.0/main/auth-service/install.sh | bash
#
# What it does (all idempotent):
#   1. Ensures python3 + venv are present.
#   2. Fetches the auth-service sources (skipped if run from inside the folder).
#   3. Creates a venv and installs the tiny requirements set.
#   4. Writes a .env from the template if one does not exist.
#   5. Prints how to run it (foreground) or install the systemd unit.
#
# Env knobs:
#   INSTALL_DIR   where to install (default: ~/auth-service)
#   REPO_RAW_BASE raw base URL to fetch sources from when piped via curl
#
set -euo pipefail

INSTALL_DIR="${INSTALL_DIR:-$HOME/auth-service}"
REPO_RAW_BASE="${REPO_RAW_BASE:-https://raw.githubusercontent.com/IamJasonBian/allocation-engine-2.0/main/auth-service}"
FILES="config.py gcp_secrets.py auth.py runner.py server.py requirements.txt env.prod.example auth-service.service"

log() { printf '\033[1;32m[install]\033[0m %s\n' "$*"; }
err() { printf '\033[1;31m[install]\033[0m %s\n' "$*" >&2; }

# --- 1. python3 ---
if ! command -v python3 >/dev/null 2>&1; then
  err "python3 not found. On Debian/Ubuntu: sudo apt-get install -y python3 python3-venv"
  exit 1
fi

# --- 2. sources ---
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]:-$0}")" 2>/dev/null && pwd || true)"
if [ -n "$SCRIPT_DIR" ] && [ -f "$SCRIPT_DIR/server.py" ]; then
  # Running from inside a checked-out copy — install in place.
  INSTALL_DIR="$SCRIPT_DIR"
  log "Using sources in $INSTALL_DIR"
else
  log "Fetching sources into $INSTALL_DIR"
  mkdir -p "$INSTALL_DIR"
  for f in $FILES; do
    curl -fsSL "$REPO_RAW_BASE/$f" -o "$INSTALL_DIR/$f"
  done
fi

cd "$INSTALL_DIR"

# --- 3. venv + deps ---
if [ ! -d venv ]; then
  log "Creating virtualenv"
  python3 -m venv venv
fi
log "Installing requirements"
./venv/bin/pip install --quiet --upgrade pip
./venv/bin/pip install --quiet -r requirements.txt

# --- 4. .env ---
if [ ! -f env.prod ]; then
  cp env.prod.example env.prod
  log "Wrote env.prod (edit it: set [gcp] project_id, [login] url, [exec] token, secret names)"
else
  log "env.prod already exists — leaving it untouched"
fi

# --- 5. next steps ---
cat <<EOF

$(log "Done.")
Run in the foreground:
    cd $INSTALL_DIR && ./venv/bin/python server.py

Install as a systemd service (auto-restart on boot):
    sed "s#__INSTALL_DIR__#$INSTALL_DIR#g; s#__USER__#$USER#g" $INSTALL_DIR/auth-service.service \\
      | sudo tee /etc/systemd/system/auth-service.service >/dev/null
    sudo systemctl daemon-reload && sudo systemctl enable --now auth-service

EOF
