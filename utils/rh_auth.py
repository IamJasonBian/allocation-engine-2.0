"""
Secure Robinhood Authentication Manager
Handles multiple accounts with environment variable support
"""

import robin_stocks.robinhood as r
from dotenv import load_dotenv
import os
import sys
from typing import Optional, Dict


class RobinhoodAuth:
    """Manages authentication for multiple Robinhood accounts"""

    def __init__(self):
        # Load environment variables from .env file
        load_dotenv()

        self.accounts = {
            'main': {
                'email': os.getenv('RH_MAIN_EMAIL'),
                'password': os.getenv('RH_MAIN_PASSWORD')
            },
            'automated': {
                'email': os.getenv('RH_AUTO_EMAIL'),
                'password': os.getenv('RH_AUTO_PASSWORD')
            }
        }

        self.active_account = os.getenv('RH_ACTIVE_ACCOUNT', 'automated')
        self.current_session = None

    def login(self, account_name: Optional[str] = None, use_stored_token: bool = True) -> Dict:
        """
        Login to Robinhood account

        Args:
            account_name: 'main' or 'automated'. If None, uses RH_ACTIVE_ACCOUNT
            use_stored_token: Try to use cached token first

        Returns:
            Login response dictionary
        """
        account = account_name or self.active_account

        if account not in self.accounts:
            raise ValueError(f"Unknown account: {account}. Use 'main' or 'automated'")

        creds = self.accounts[account]

        # Validate credentials are set
        if not creds['email'] or not creds['password']:
            raise ValueError(
                f"Credentials not set for {account} account. "
                f"Please update your .env file with RH_{account.upper()}_EMAIL and RH_{account.upper()}_PASSWORD"
            )

        # Logout any existing session
        try:
            r.logout()
        except:
            pass

        # Try to use stored token first
        if use_stored_token:
            try:
                print(f"🔄 Attempting to login with stored token for '{account}' account...")
                login_response = r.login(pickle_name=f'rh_{account}')
                print(f"✅ Successfully logged in with stored token: {creds['email']}")
                self.current_session = account
                return login_response
            except Exception as e:
                print(f"⚠️  Stored token failed or expired: {e}")
                print(f"🔄 Logging in with credentials...")

        # Fresh login with credentials
        try:
            login_response = r.login(
                creds['email'],
                creds['password'],
                store_session=True,
                pickle_name=f'rh_{account}'
            )
            print(f"✅ Successfully logged in: {creds['email']}")
            self.current_session = account
            return login_response
        except Exception as e:
            print(f"❌ Login failed: {e}")
            sys.exit(1)

    def logout(self):
        """Logout from current Robinhood session"""
        try:
            r.logout()
            print(f"✅ Logged out from {self.current_session} account")
            self.current_session = None
        except Exception as e:
            print(f"⚠️  Logout warning: {e}")

    def switch_account(self, account_name: str):
        """Switch to a different account"""
        print(f"🔄 Switching from {self.current_session} to {account_name}...")
        return self.login(account_name)

    def get_account_info(self) -> Dict:
        """Get current account information"""
        try:
            profile = r.profiles.load_account_profile()
            portfolio = r.profiles.load_portfolio_profile()

            return {
                'account_number': profile.get('account_number', 'N/A'),
                'equity': portfolio.get('equity', 'N/A'),
                'market_value': portfolio.get('market_value', 'N/A'),
                'buying_power': portfolio.get('buying_power', 'N/A'),
            }
        except Exception as e:
            print(f"❌ Failed to fetch account info: {e}")
            return {}


def main():
    """Example usage"""
    auth = RobinhoodAuth()

    # Login to automated account (default)
    auth.login()

    # Get account info
    info = auth.get_account_info()
    print(f"\n📊 Account Info:")
    for key, value in info.items():
        print(f"   {key}: {value}")

    # Logout
    auth.logout()


if __name__ == "__main__":
    main()
