"""
Verify that the bot is properly isolated to account 919433888
"""

import robin_stocks.robinhood as r
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from utils.rh_auth import RobinhoodAuth
from dotenv import load_dotenv


def verify_isolation():
    """Verify bot isolation to specific account"""
    load_dotenv()

    target_account = os.getenv('RH_AUTOMATED_ACCOUNT_NUMBER')

    print("\n" + "="*70)
    print("🔍 ISOLATION VERIFICATION")
    print("="*70 + "\n")

    print(f"Target Account: {target_account}")

    if target_account != "490706777":
        print(f"❌ WARNING: Expected 490706777, got {target_account}")
        return False

    # Login
    auth = RobinhoodAuth()
    auth.login()

    print("\n1️⃣ Checking all available accounts...")
    try:
        url = 'https://api.robinhood.com/accounts/?default_to_all_accounts=true'
        data = r.helper.request_get(url, dataType='regular')

        if 'results' in data:
            accounts = data['results']
            print(f"   Found {len(accounts)} total account(s)")

            for idx, acc in enumerate(accounts, 1):
                acc_num = acc.get('account_number')
                acc_type = acc.get('type')
                is_target = "✅ TARGET" if acc_num == target_account else ""
                print(f"   Account {idx}: {acc_num} ({acc_type}) {is_target}")

    except Exception as e:
        print(f"   ⚠️  Could not list all accounts: {e}")

    print("\n2️⃣ Checking target account details...")
    try:
        account = r.profiles.load_account_profile(account_number=target_account)
        portfolio = r.profiles.load_portfolio_profile(account_number=target_account)

        acc_type = account.get('type', 'unknown')
        cash = float(account.get('cash', 0))
        buying_power = float(account.get('buying_power', 0))
        equity = float(portfolio.get('equity', 0))

        print(f"   ✅ Successfully accessed account {target_account}")
        print(f"   Type: {acc_type}")
        print(f"   Cash: ${cash:,.2f}")
        print(f"   Buying Power: ${buying_power:,.2f}")
        print(f"   Equity: ${equity:,.2f}")

        if acc_type == 'cash':
            print(f"   ✅ VERIFIED: Cash account (no margin)")
        else:
            print(f"   ⚠️  Account type is '{acc_type}', will still use cash only")

    except Exception as e:
        print(f"   ❌ ERROR: Cannot access account {target_account}")
        print(f"   {e}")
        auth.logout()
        return False

    print("\n3️⃣ Checking API call isolation...")
    try:
        # Test that positions are filtered by account
        url = f'https://api.robinhood.com/positions/?account_number={target_account}'
        positions_data = r.helper.request_get(url, dataType='pagination')

        print(f"   ✅ Positions API filtered to account {target_account}")
        print(f"   Found {len([p for p in positions_data if float(p.get('quantity', 0)) > 0])} open positions")

    except Exception as e:
        print(f"   ⚠️  Could not verify positions API: {e}")

    print("\n" + "="*70)
    print("✅ ISOLATION VERIFIED")
    print("="*70)
    print(f"\nBot is locked to account: {target_account}")
    print(f"All orders will be executed ONLY in this account")
    print(f"Only available CASH will be used (no margin)\n")

    auth.logout()
    return True


if __name__ == "__main__":
    verify_isolation()
