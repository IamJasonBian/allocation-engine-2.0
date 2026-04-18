#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"
cd "$PROJECT_DIR"

echo "=== Allocation Engine 2.0 — Setup ==="

# 1. Python venv
if [ ! -d ".venv" ]; then
    echo "Creating virtual environment..."
    python3 -m venv .venv
else
    echo "Virtual environment already exists"
fi

source .venv/bin/activate
echo "Using Python: $(python --version)"

# 2. Dependencies
echo "Installing dependencies..."
pip install -q --upgrade pip
pip install -q -r requirements.txt

# 3. .env file
if [ ! -f ".env" ]; then
    cp .env.example .env
    echo "Created .env from .env.example — fill in your credentials"
else
    echo ".env already exists, skipping"
fi

# 4. Session directory for Robinhood pickle
mkdir -p ~/.tokens
echo "Session directory ready: ~/.tokens"

# 5. Schema checks
echo ""
echo "Running deploy checks..."
python scripts/check_schemas.py

echo ""
echo "=== Setup complete ==="
echo "Start the server:  source .venv/bin/activate && gunicorn app.wsgi:application"
echo "Or dev mode:        source .venv/bin/activate && python main.py serve"
