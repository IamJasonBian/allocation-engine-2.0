"""Enable options trading on the Alpaca account.

Usage:
    python scripts/enable_alpaca_options.py          # show current config
    python scripts/enable_alpaca_options.py --level 2 # set options level (0-3)

Levels:
    0 = Disabled
    1 = Covered Call / Cash-Secured Put
    2 = Long Call / Long Put (+ Level 1)
    3 = Spreads / Straddles  (+ Levels 1-2)

Paper accounts default to Level 3. Live accounts need dashboard approval first.
"""

import argparse
import os
import sys

from dotenv import load_dotenv

load_dotenv()

from alpaca.common.exceptions import APIError
from alpaca.trading.client import TradingClient

DASHBOARD_URL = "https://app.alpaca.markets/account/options"


def main():
    parser = argparse.ArgumentParser(description="Enable Alpaca options trading")
    parser.add_argument(
        "--level",
        type=int,
        choices=[0, 1, 2, 3],
        help="Options trading level to set (0=disabled, 1=covered, 2=long, 3=spreads)",
    )
    args = parser.parse_args()

    api_key = os.environ["ALPACA_API_KEY"]
    secret_key = os.environ["ALPACA_SECRET_KEY"]
    paper = os.getenv("ALPACA_PAPER", "true").lower() == "true"

    client = TradingClient(api_key, secret_key, paper=paper)

    # Show current account info
    acct = client.get_account()
    approved_level = getattr(acct, "options_approved_level", None)
    trading_level = getattr(acct, "options_trading_level", None)
    buying_power = getattr(acct, "options_buying_power", None)

    print(f"Account:  {acct.account_number}")
    print(f"Status:   {acct.status}")
    print(f"Paper:    {paper}")
    print(f"Equity:   ${float(acct.equity):,.2f}")
    print()
    print(f"Options approved level:  {approved_level}")
    print(f"Options trading level:   {trading_level}")
    print(f"Options buying power:    {buying_power}")
    print()

    # Show current configuration
    config = client.get_account_configurations()
    max_level = getattr(config, "max_options_trading_level", None)
    print(f"Max options trading level (config): {max_level}")
    print()

    # Check if options are approved
    if approved_level is None and not paper:
        print("Options are NOT approved on this live account.")
        print(f"To request approval, visit: {DASHBOARD_URL}")
        print()
        print("Steps:")
        print("  1. Log in to the Alpaca dashboard")
        print("  2. Go to Account > Configure > Options")
        print("  3. Complete the options trading application")
        print("  4. Once approved, re-run this script with --level")
        if args.level is not None:
            print()
            print(f"Cannot set level {args.level} until options are approved.")
        return

    if args.level is None:
        print("Levels:")
        print("  0 = Disabled")
        print("  1 = Covered Call / Cash-Secured Put")
        print("  2 = Long Call / Long Put")
        print("  3 = Spreads / Straddles")
        print()
        print("To change: python scripts/enable_alpaca_options.py --level <0-3>")
        return

    if max_level is not None and int(max_level) == args.level:
        print(f"Already at level {args.level}, no change needed.")
        return

    # Update options trading level
    print(f"Setting max options trading level to {args.level}...")
    try:
        config.max_options_trading_level = args.level
        updated = client.set_account_configurations(config)
        new_level = getattr(updated, "max_options_trading_level", None)
        print(f"Updated max options trading level: {new_level}")
        print("Done.")
    except APIError as e:
        print(f"Error: {e}")
        if "option trading disabled" in str(e).lower():
            print()
            print(f"Options must be approved first. Visit: {DASHBOARD_URL}")


if __name__ == "__main__":
    main()
