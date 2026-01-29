"""
Test which accounts can place orders
"""

import sys
import os

import robin_stocks.robinhood as r

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from utils.rh_auth import RobinhoodAuth  # noqa: E402


def test_account(account_number):
    """Test if account can place orders"""
    print(f"\n{'='*70}")
    print(f"Testing Account: {account_number}")
    print(f"{'='*70}")

    try:
        # Get account info
        account = r.profiles.load_account_profile(account_number=account_number)
        print(f"✅ Account Type: {account.get('type')}")
        print(f"✅ Cash: ${float(account.get('cash', 0)):.2f}")

        # Try placing a test order (will fail but shows error)
        print("\n🔍 Testing order placement...")
        order = r.orders.order_buy_limit(
            symbol='AAPL',
            quantity=1,
            limitPrice=250.00,
            account_number=account_number
        )

        if order and 'id' in order:
            print("✅ CAN TRADE - Order placed successfully")
            print(f"   Order ID: {order['id']}")
            print(f"   State: {order['state']}")

            # Cancel the test order immediately
            try:
                r.orders.cancel_stock_order(order['id'])
                print("   ✅ Test order cancelled")
            except Exception:
                print("   ⚠️  Could not cancel - check Robinhood app")

        elif order and 'non_field_errors' in order:
            print("❌ CANNOT TRADE")
            print(f"   Reason: {order['non_field_errors'][0]}")
        else:
            print(f"⚠️  Unknown response: {order}")

    except Exception as e:
        print(f"❌ Error: {e}")

    print(f"{'='*70}\n")


def main():
    # Login
    auth = RobinhoodAuth()
    auth.login()

    print("\n" + "="*70)
    print("🔍 TESTING ALL ACCOUNTS FOR TRADING CAPABILITY")
    print("="*70)

    # Get all accounts
    url = 'https://api.robinhood.com/accounts/?default_to_all_accounts=true'
    data = r.helper.request_get(url, dataType='regular')

    if 'results' in data:
        accounts = data['results']
        print(f"\nFound {len(accounts)} accounts\n")

        for acc in accounts:
            account_number = acc.get('account_number')
            account_type = acc.get('type')
            cash = float(acc.get('cash', 0))

            print(f"Account: {account_number} ({account_type}, ${cash:.2f} cash)")

        print("\n" + "="*70)
        print("Now testing each account...")
        print("="*70)

        for acc in accounts:
            account_number = acc.get('account_number')
            test_account(account_number)

    auth.logout()


if __name__ == "__main__":
    main()
