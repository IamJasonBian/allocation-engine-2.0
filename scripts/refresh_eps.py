#!/usr/bin/env python3
"""Refresh the EPS dataset: fetch, trim to the retention window, export.

Writes data/eps.sqlite3 and data/eps.json (both gitignored). The JSON is what
allocation-gym-2.0 reads.

Needs yfinance, which is deliberately not in requirements.txt — it is only
required to refresh the dataset, never to serve it:
    python -m venv .venv && .venv/bin/pip install yfinance
    .venv/bin/python scripts/refresh_eps.py

Usage:
  python scripts/refresh_eps.py                    # refresh the configured cohort
  python scripts/refresh_eps.py --tickers NVDA,AVGO
  python scripts/refresh_eps.py --quarters 8       # widen the history window
  python scripts/refresh_eps.py --show             # print without fetching
"""

import argparse
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.earnings_store import EarningsStore   # noqa: E402
from app.earnings_writer import (               # noqa: E402
    DEFAULT_TICKERS,
    HISTORY_QUARTERS,
    refresh,
)


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--tickers", default="", help="comma-separated override")
    p.add_argument("--quarters", type=int, default=HISTORY_QUARTERS,
                   help=f"reported quarters retained per ticker (default {HISTORY_QUARTERS})")
    p.add_argument("--show", action="store_true", help="dump the store, no fetch")
    args = p.parse_args()

    store = EarningsStore()

    if args.show:
        print(json.dumps({"tickers": store.tickers(), "rows": store.count(),
                          "last_refresh": store.get_meta("last_refresh"),
                          "eps": store.all()}, indent=2))
        return 0

    tickers = [t.strip().upper() for t in args.tickers.split(",") if t.strip()]
    out = refresh(tickers=tickers or DEFAULT_TICKERS,
                  store=store, history_quarters=args.quarters)

    print(f"{out['written']} rows across {len(out['tickers'])} tickers")
    print(f"sqlite -> {os.path.abspath(store.path)}")
    print(f"json   -> {out['exported']}")
    if out["failures"]:
        print("failed: " + ", ".join(f"{f['ticker']} ({f['error']})"
                                     for f in out["failures"]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
