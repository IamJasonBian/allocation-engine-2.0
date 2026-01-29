"""
Robinhood Multiple Account/Profile Manager
Handles switching between different Robinhood investment accounts
"""

import sys
import os
import json

import robin_stocks.robinhood as r

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from utils.rh_auth import RobinhoodAuth  # noqa: E402


class ProfileManager:
    """Manages multiple Robinhood investment accounts/profiles"""

    def __init__(self):
        self.auth = RobinhoodAuth()
        self.accounts = []
        self.current_account = None

    def login(self):
        """Login and fetch all accounts"""
        self.auth.login()
        self.fetch_all_accounts()
        return self.accounts

    def fetch_all_accounts(self):
        """
        Fetch all investment accounts associated with this login
        Robinhood allows up to 10 separate investment accounts per user
        """
        try:
            # Get all accounts using the default_to_all_accounts parameter
            # This is handled by account_profile_url() in robin-stocks
            url = 'https://api.robinhood.com/accounts/?default_to_all_accounts=true'
            data = r.helper.request_get(url, dataType='regular')

            if 'results' in data:
                self.accounts = data['results']
                print(f"✅ Found {len(self.accounts)} account(s)")
                return self.accounts
            else:
                # Fallback to single account
                account = r.profiles.load_account_profile()
                self.accounts = [account] if account else []
                print("✅ Found 1 account")
                return self.accounts

        except Exception as e:
            print(f"❌ Error fetching accounts: {e}")
            return []

    def list_all_accounts(self):
        """Display all available accounts with details"""
        if not self.accounts:
            print("No accounts found. Please login first.")
            return

        print("\n" + "="*80)
        print("📋 ALL ROBINHOOD ACCOUNTS")
        print("="*80 + "\n")

        for idx, account in enumerate(self.accounts, 1):
            account_number = account.get('account_number', 'N/A')
            account_type = account.get('type', 'N/A')
            buying_power = float(account.get('buying_power', 0))
            cash = float(account.get('cash', 0))
            is_deactivated = account.get('deactivated', False)

            # Try to get the profile/portfolio for this account
            try:
                portfolio = r.profiles.load_portfolio_profile(account_number=account_number)
                equity = float(portfolio.get('equity', 0))
                market_value = float(portfolio.get('market_value', 0))
            except Exception:
                equity = 0
                market_value = 0

            print(f"Account #{idx}")
            print(f"{'─'*80}")
            print(f"   Account Number: {account_number}")
            print(f"   Type: {account_type}")
            print(f"   Status: {'❌ Deactivated' if is_deactivated else '✅ Active'}")
            print(f"   Equity: ${equity:,.2f}")
            print(f"   Market Value: ${market_value:,.2f}")
            print(f"   Cash: ${cash:,.2f}")
            print(f"   Buying Power: ${buying_power:,.2f}")
            print()

        return self.accounts

    def get_account_by_number(self, account_number):
        """Get specific account details by account number"""
        try:
            account = r.profiles.load_account_profile(account_number=account_number)
            portfolio = r.profiles.load_portfolio_profile(account_number=account_number)

            print(f"\n{'='*80}")
            print(f"📊 ACCOUNT DETAILS: {account_number}")
            print(f"{'='*80}\n")

            print("Account Info:")
            print(f"   Account Number: {account.get('account_number', 'N/A')}")
            print(f"   Type: {account.get('type', 'N/A')}")
            print(f"   Created: {account.get('created_at', 'N/A')[:10]}")

            print("\nPortfolio:")
            print(f"   Equity: ${float(portfolio.get('equity', 0)):,.2f}")
            print(f"   Market Value: ${float(portfolio.get('market_value', 0)):,.2f}")
            print(f"   Buying Power: ${float(account.get('buying_power', 0)):,.2f}")
            print(f"   Cash: ${float(account.get('cash', 0)):,.2f}")

            print("\nOptions:")
            print(f"   Option Level: {account.get('option_level', 'N/A')}")
            print(f"   Fractionals Eligible: {account.get('eligible_for_fractionals', False)}")

            self.current_account = account_number
            return account

        except Exception as e:
            print(f"❌ Error fetching account {account_number}: {e}")
            return None

    def find_account_by_name(self, name_hint):
        """
        Try to find an account by searching for a name/identifier
        Note: Robinhood doesn't store custom account names in the API,
        so this searches through available fields
        """
        if not self.accounts:
            print("No accounts loaded. Please run login() first.")
            return None

        print(f"\n🔍 Searching for account matching: '{name_hint}'")

        for account in self.accounts:
            account_number = account.get('account_number', '')
            account_type = account.get('type', '')

            # Check if name_hint matches account number or type
            if name_hint.lower() in account_number.lower() or name_hint.lower() in account_type.lower():
                print(f"✅ Found match: {account_number} ({account_type})")
                return account

        print(f"❌ No account found matching '{name_hint}'")
        return None

    def use_robinhood_web_interface(self):
        """
        Instructions for finding account IDs via Robinhood web interface
        """
        print("\n" + "="*80)
        print("🌐 FINDING ACCOUNT IDS VIA ROBINHOOD WEB INTERFACE")
        print("="*80 + "\n")

        print("To find your account numbers and profile names:")
        print("\n1. Login to https://robinhood.com")
        print("2. Open browser DevTools (F12 or Cmd+Option+I)")
        print("3. Go to 'Network' tab")
        print("4. Navigate to 'Account' page in Robinhood")
        print("5. Look for API calls to:")
        print("   • https://api.robinhood.com/accounts/")
        print("   • https://api.robinhood.com/portfolios/")
        print("\n6. Click on the request and view the 'Response' tab")
        print("7. Look for 'account_number' fields in the JSON response")
        print("\n8. Copy the account_number and use get_account_by_number()")

        print("\n" + "─"*80)
        print("Alternative: Export account data")
        print("─"*80)
        print("\nYou can also run this script and copy all account numbers")
        print("displayed in list_all_accounts() output.")
        print("="*80 + "\n")

    def save_account_config(self, filename='accounts_config.json'):
        """Save account numbers to a config file for future reference"""
        if not self.accounts:
            print("No accounts to save")
            return

        config = {
            'accounts': [
                {
                    'account_number': acc.get('account_number'),
                    'type': acc.get('type'),
                    'alias': f"Account_{idx}"  # User can edit this manually
                }
                for idx, acc in enumerate(self.accounts, 1)
            ]
        }

        with open(filename, 'w') as f:
            json.dump(config, f, indent=2)

        print(f"✅ Saved account configuration to {filename}")
        print("   You can edit this file to add custom aliases for your accounts")

    def logout(self):
        """Logout from Robinhood"""
        self.auth.logout()


def main():
    """Example usage"""
    manager = ProfileManager()

    # Login and fetch all accounts
    print("🔐 Logging in...")
    manager.login()

    # List all accounts
    manager.list_all_accounts()

    # Save configuration for future reference
    manager.save_account_config()

    # Show how to use web interface
    manager.use_robinhood_web_interface()

    # If you know your account number, you can get specific details:
    # manager.get_account_by_number('YOUR_ACCOUNT_NUMBER')

    # Logout
    manager.logout()


if __name__ == "__main__":
    main()
