#!/usr/bin/env python3
"""End-to-end dry run — confirms the whole flow against LIVE Robinhood.

Steps:
  1. Secret Manager -> frozen Credentials (never printed)
  2. authenticate() -> Session  (you approve the push on your device)
  3. read active percentage trailing-stop orders (READ-ONLY)
  4. build a place + replace payload in DRY-RUN mode (NOTHING is submitted)

No order is ever POSTed. Run on the box:  ./venv/bin/python dryrun.py
"""

import json
import logging

import os

import config
import robinhood
import session as session_mgr

# INFO by default so raw API bodies (which include the access token on the
# successful oauth2/token response) are NOT dumped. DRYRUN_DEBUG=1 for verbose
# diagnosis — safe to use while auth is still failing (no token issued yet).
_level = logging.DEBUG if os.getenv("DRYRUN_DEBUG") else logging.INFO
logging.basicConfig(level=_level,
                    format="%(asctime)s %(levelname)-7s %(name)s  %(message)s",
                    datefmt="%H:%M:%S")
log = logging.getLogger("dryrun")


def main():
    profile = config.DEFAULT_PROFILE
    print(f"\n=== 1. credentials from Secret Manager (project={config.GCP_PROJECT_ID}) ===")
    creds = session_mgr.load_credentials(profile)
    print(f"  loaded: {creds!r}")          # __repr__ masks the secrets
    print(f"  device_token: {creds.device_token}")

    print("\n=== 2. authenticate (approve the push on your device) ===")
    deadline = int(os.getenv("DEADLINE", "150"))
    result = robinhood.authenticate(creds, approval_deadline=deadline)
    print(f"  status={result.status} error_code={result.error_code} detail={result.detail}")
    if result.status != "OK" or result.session is None:
        print("  >>> auth did not complete; stopping.")
        return
    session = result.session
    session_mgr.save(profile, session)
    print(f"  session: {session!r}")
    print(f"  account_url: {session.account_url}")

    print("\n=== 3. active percentage trailing-stop orders (read-only) ===")
    orders = robinhood.get_trailing_stop_orders(session)
    print(f"  found {len(orders)} active percentage trailing-stop order(s)")
    for o in orders:
        print(json.dumps({
            "id": o.get("id"),
            "state": o.get("state"),
            "side": o.get("side"),
            "trigger": o.get("trigger"),
            "trailing_peg": o.get("trailing_peg"),
            "stop_price": o.get("stop_price"),
            "instrument": o.get("instrument"),
        }, indent=2))

    print("\n=== 4. place/replace payload (DRY RUN — nothing submitted) ===")
    sample = orders[0] if orders else None
    instrument = sample["instrument"] if sample else "https://api.robinhood.com/instruments/<UUID>/"
    symbol = sample.get("symbol", "<SYMBOL>") if sample else "<SYMBOL>"
    place = robinhood.place_trailing_stop(
        session,
        robinhood.build_trailing_stop_payload(
            account_url=session.account_url, instrument_url=instrument,
            symbol=symbol, side="sell", quantity="1", trail_percent="5.00"),
        dry_run=True)
    print("  PLACE:")
    print(json.dumps(place, indent=2))
    if sample:
        replace = robinhood.replace_trailing_stop(
            session, sample["id"],
            robinhood.build_trailing_stop_payload(
                account_url=session.account_url, instrument_url=instrument,
                symbol=symbol, side=sample.get("side", "sell"),
                quantity=sample.get("quantity", "1"), trail_percent="6.00"),
            dry_run=True)
        print("  REPLACE:")
        print(json.dumps(replace, indent=2))

    print("\n=== done — auth + read confirmed live; place/replace shown as dry-run ===")


if __name__ == "__main__":
    main()
