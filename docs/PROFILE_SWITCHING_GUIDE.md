# Robinhood Profile/Account Switching Guide

## Understanding Robinhood Accounts

Robinhood supports **multiple investment accounts** under a single login (up to 10 accounts). Each account has:
- Unique **account number** (e.g., `5AB12345`)
- **Account type** (individual, IRA, etc.)
- Separate portfolio, equity, and buying power

## Three Methods to Access Specific Accounts

### Method 1: Use `manage_profiles.py` (Recommended)

This script automatically discovers all your accounts:

```bash
cd ~/robinhood-trading
python manage_profiles.py
```

**What it does:**
1. Logs in with your credentials
2. Fetches ALL accounts associated with your login
3. Displays account numbers, types, equity, and buying power
4. Saves account configuration to `accounts_config.json`

**Output example:**
```
📋 ALL ROBINHOOD ACCOUNTS
════════════════════════════════════════════════════════════════════════════════

Account #1
────────────────────────────────────────────────────────────────────────────────
   Account Number: 5AB12345
   Type: cash
   Status: ✅ Active
   Equity: $10,543.21
   Market Value: $8,543.21
   Cash: $2,000.00
   Buying Power: $2,000.00

Account #2
────────────────────────────────────────────────────────────────────────────────
   Account Number: 5AB67890
   Type: margin
   Status: ✅ Active
   Equity: $25,123.45
   ...
```

### Method 2: Find Account Number via Robinhood Web Interface

#### Steps:

1. **Login to Robinhood**
   - Go to https://robinhood.com
   - Login with your credentials

2. **Open Browser DevTools**
   - Chrome/Edge: Press `F12` or `Cmd+Option+I` (Mac)
   - Firefox: Press `F12`

3. **Monitor Network Traffic**
   - Click the **Network** tab in DevTools
   - Navigate to your **Account** page in Robinhood

4. **Find API Calls**
   - Look for requests to:
     - `https://api.robinhood.com/accounts/`
     - `https://api.robinhood.com/portfolios/`

5. **View Response**
   - Click on the request
   - Click **Response** or **Preview** tab
   - Look for JSON like this:

   ```json
   {
     "results": [
       {
         "account_number": "5AB12345",
         "type": "cash",
         "deactivated": false,
         ...
       },
       {
         "account_number": "5AB67890",
         "type": "margin",
         "deactivated": false,
         ...
       }
     ]
   }
   ```

6. **Copy Account Number**
   - Copy the `account_number` value (e.g., `5AB12345`)

### Method 3: Use robin-stocks API Directly

Once you have the account number, use it in your code:

```python
import robin_stocks.robinhood as r
from rh_auth import RobinhoodAuth

# Login
auth = RobinhoodAuth()
auth.login()

# Access specific account by number
account_number = "5AB12345"  # Your "automated" account number

# Get account details
account = r.profiles.load_account_profile(account_number=account_number)
portfolio = r.profiles.load_portfolio_profile(account_number=account_number)

print(f"Account: {account['account_number']}")
print(f"Equity: ${portfolio['equity']}")
print(f"Buying Power: ${account['buying_power']}")
```

## Working with Specific Accounts in Your Scripts

### Option A: Hardcode Account Number (Simple)

```python
from manage_profiles import ProfileManager

manager = ProfileManager()
manager.login()

# Use your "automated" account
automated_account_number = "5AB12345"  # Replace with your actual number
manager.get_account_by_number(automated_account_number)
```

### Option B: Store in .env File (Recommended)

Add to `.env`:
```bash
# Account Numbers
RH_AUTOMATED_ACCOUNT_NUMBER=5AB12345
RH_MAIN_ACCOUNT_NUMBER=5AB67890
```

Then in your code:
```python
import os
from dotenv import load_dotenv
import robin_stocks.robinhood as r

load_dotenv()

# Get account numbers from environment
automated_account = os.getenv('RH_AUTOMATED_ACCOUNT_NUMBER')
main_account = os.getenv('RH_MAIN_ACCOUNT_NUMBER')

# Use specific account
account = r.profiles.load_account_profile(account_number=automated_account)
```

### Option C: Use accounts_config.json (Most Flexible)

After running `manage_profiles.py`, edit `accounts_config.json`:

```json
{
  "accounts": [
    {
      "account_number": "5AB12345",
      "type": "cash",
      "alias": "automated"
    },
    {
      "account_number": "5AB67890",
      "type": "margin",
      "alias": "main"
    }
  ]
}
```

Then load it:
```python
import json

with open('accounts_config.json') as f:
    config = json.load(f)

# Find account by alias
automated = next(a for a in config['accounts'] if a['alias'] == 'automated')
account_number = automated['account_number']
```

## Updated Trading Bot with Account Support

```python
import robin_stocks.robinhood as r
from rh_auth import RobinhoodAuth
import os

class AccountAwareTradingBot:
    def __init__(self, account_number=None):
        self.auth = RobinhoodAuth()
        self.auth.login()
        self.account_number = account_number or os.getenv('RH_AUTOMATED_ACCOUNT_NUMBER')

    def get_portfolio(self):
        """Get portfolio for specific account"""
        if self.account_number:
            account = r.profiles.load_account_profile(account_number=self.account_number)
            portfolio = r.profiles.load_portfolio_profile(account_number=self.account_number)
        else:
            # Default to first account
            account = r.profiles.load_account_profile()
            portfolio = r.profiles.load_portfolio_profile()

        return {
            'account_number': account['account_number'],
            'equity': portfolio['equity'],
            'buying_power': account['buying_power']
        }

# Use it
bot = AccountAwareTradingBot(account_number="5AB12345")
portfolio = bot.get_portfolio()
print(f"Automated Account Equity: ${portfolio['equity']}")
```

## Undocumented API Endpoints (Advanced)

robin-stocks uses these Robinhood API endpoints:

### Get All Accounts
```
GET https://api.robinhood.com/accounts/?default_to_all_accounts=true
```

### Get Specific Account
```
GET https://api.robinhood.com/accounts/{account_number}/
```

### Get Portfolio for Account
```
GET https://api.robinhood.com/portfolios/{account_number}/
```

### Get Positions for Account
```
GET https://api.robinhood.com/positions/?account_number={account_number}
```

You can use these directly with `r.helper.request_get()`:

```python
import robin_stocks.robinhood as r

# Get all accounts
url = 'https://api.robinhood.com/accounts/?default_to_all_accounts=true'
accounts = r.helper.request_get(url, dataType='regular')
print(accounts['results'])
```

## Quick Reference Commands

```bash
# Discover all accounts
python manage_profiles.py

# List accounts with details
python list_accounts.py

# Test connection to specific account (edit script first)
python trading_bot.py
```

## Summary

✅ **robin-stocks DOES support multiple accounts** via `account_number` parameter
✅ **Find account numbers** using `manage_profiles.py` or web DevTools
✅ **Store account numbers** in `.env` or `accounts_config.json`
✅ **Use account-specific calls** with `load_account_profile(account_number=...)`

The "automated" profile is simply one of your Robinhood investment accounts with a specific account number. Use the scripts above to discover its account number!
