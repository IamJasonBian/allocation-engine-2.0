"""
List all configured Robinhood accounts and their details
"""

import sys
import os

import robin_stocks.robinhood as r
from dotenv import load_dotenv

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from utils.rh_auth import RobinhoodAuth  # noqa: E402


def list_accounts():
    """Display all configured accounts and their information"""
    load_dotenv()

    print("\n" + "="*60)
    print("📋 CONFIGURED ROBINHOOD ACCOUNTS")
    print("="*60 + "\n")

    accounts = {
        'main': {
            'email': os.getenv('RH_MAIN_EMAIL'),
            'password': os.getenv('RH_MAIN_PASSWORD')
        },
        'automated': {
            'email': os.getenv('RH_AUTO_EMAIL'),
            'password': os.getenv('RH_AUTO_PASSWORD')
        }
    }

    active_account = os.getenv('RH_ACTIVE_ACCOUNT', 'automated')

    for account_name, creds in accounts.items():
        is_active = "✓ ACTIVE" if account_name == active_account else ""
        has_creds = bool(creds['email'] and creds['password']
                         and 'example.com' not in creds['email'])

        print(f"{'='*60}")
        print(f"📱 {account_name.upper()} ACCOUNT {is_active}")
        print(f"{'='*60}")
        print(f"   Email: {creds['email']}")
        print(f"   Configured: {'✅ Yes' if has_creds else '❌ No'}")

        if has_creds:
            try:
                # Try to login and get details
                print("   Fetching account details...")
                auth = RobinhoodAuth()
                auth.login(account_name)

                info = auth.get_account_info()
                print(f"   Account #: {info.get('account_number', 'N/A')}")
                print(f"   Equity: ${float(info.get('equity', 0)):,.2f}")
                print(f"   Market Value: ${float(info.get('market_value', 0)):,.2f}")
                print(f"   Buying Power: ${float(info.get('buying_power', 0)):,.2f}")

                # Get positions count
                positions = r.get_open_stock_positions()
                print(f"   Open Positions: {len(positions)}")

                auth.logout()
                print("   Status: ✅ Connected")

            except Exception as e:
                print("   Status: ❌ Failed to connect")
                print(f"   Error: {str(e)[:50]}...")

        print()


if __name__ == "__main__":
    list_accounts()
