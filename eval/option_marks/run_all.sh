#!/usr/bin/env bash
# Single entrypoint for Chanto cloud/local runs.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
cd "$ROOT"
export PYTHONPATH=.

python3 eval/option_marks/run_eval.py
python3 eval/option_marks/run_eval.py --live
python3 scripts/pull_orders_and_mark.py
python3 -m pytest tests/test_option_mark.py tests/test_option_pricer.py tests/test_bounce_intents.py -q
