"""
List all Robinhood profiles within your account
"""

import sys
import os

import robin_stocks.robinhood as r

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from utils.rh_auth import RobinhoodAuth  # noqa: E402


def list_profiles():
    """List all profiles in the Robinhood account"""
    auth = RobinhoodAuth()
    auth.login()

    print("\n" + "="*60)
    print("ROBINHOOD PROFILES")
    print("="*60 + "\n")

    try:
        # Get account profile
        profile = r.profiles.load_account_profile()
        print("Account Profile:")
        print(f"   Account Number: {profile.get('account_number', 'N/A')}")
        print(f"   Type: {profile.get('type', 'N/A')}")
        print(f"   Created: {profile.get('created_at', 'N/A')[:10]}")

        # Get portfolio profile
        portfolio = r.profiles.load_portfolio_profile()
        print("\nPortfolio Profile:")
        print(f"   Equity: ${float(portfolio.get('equity', 0)):,.2f}")
        print(f"   Extended Hours Equity: ${float(portfolio.get('extended_hours_equity', 0)):,.2f}")

        # Check if there are multiple profiles/accounts
        # Some Robinhood accounts have multiple investment profiles
        print("\nInvestment Profiles:")

        # Get user info
        user = r.profiles.load_user_profile()
        print(f"   Username: {user.get('username', 'N/A')}")
        print(f"   Email: {user.get('email', 'N/A')}")

        # Try to get all accounts (if multiple exist)
        try:
            accounts_data = r.get_all_positions()
            print(f"\n   Total positions across all profiles: {len(accounts_data)}")
        except Exception:
            pass

    except Exception as e:
        print(f"[ERR] Error fetching profiles: {e}")
    finally:
        auth.logout()


if __name__ == "__main__":
    list_profiles()
