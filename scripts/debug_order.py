"""
Debug order placement to see actual error
"""

import sys
import os

import robin_stocks.robinhood as r

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from utils.safe_cash_bot import SafeCashBot  # noqa: E402


def debug_order():
    """Debug why orders are failing"""

    bot = SafeCashBot()

    print("\n" + "="*70)
    print("🔍 DEBUG: Order Placement")
    print("="*70 + "\n")

    # Check account
    print("1️⃣ Checking account access...")
    try:
        account = r.profiles.load_account_profile(account_number='490706777')
        print(f"   ✅ Account: {account.get('account_number')}")
        print(f"   Type: {account.get('type')}")
        print(f"   Cash: ${float(account.get('cash', 0)):.2f}")
    except Exception as e:
        print(f"   ❌ Error: {e}")
        bot.auth.logout()
        return

    # Get AAPL quote
    print("\n2️⃣ Getting AAPL quote...")
    try:
        quote = r.stocks.get_quotes('AAPL')[0]
        price = float(r.stocks.get_latest_price('AAPL')[0])
        print(f"   ✅ Current price: ${price:.2f}")
        print(f"   Ask price: ${float(quote.get('ask_price', 0)):.2f}")
    except Exception as e:
        print(f"   ❌ Error: {e}")
        bot.auth.logout()
        return

    # Try placing order with detailed error catching
    print("\n3️⃣ Attempting to place order...")
    print("   Symbol: AAPL")
    print("   Quantity: 1")
    print(f"   Limit: ${price * 1.005:.2f}")
    print("   Account: 490706777")

    try:
        # Direct API call
        order = r.orders.order_buy_limit(
            symbol='AAPL',
            quantity=1,
            limitPrice=price * 1.005,
            account_number='490706777'
        )

        print("\n   ✅ Order Response:")
        print(f"   Type: {type(order)}")
        print(f"   Content: {order}")

        if order:
            print(f"\n   Order ID: {order.get('id', 'N/A')}")
            print(f"   State: {order.get('state', 'N/A')}")
            print(f"   Symbol: {order.get('symbol', 'N/A')}")
            print(f"   Quantity: {order.get('quantity', 'N/A')}")
            print(f"   Price: {order.get('price', 'N/A')}")

            # Check for reject reason
            if 'reject_reason' in order:
                print(f"   ⚠️  Reject Reason: {order['reject_reason']}")
        else:
            print("   ❌ Order returned None/empty")

    except Exception as e:
        print("\n   ❌ Exception occurred:")
        print(f"   Error: {e}")
        print(f"   Type: {type(e)}")

        import traceback
        print("\n   Full traceback:")
        traceback.print_exc()

    bot.auth.logout()


if __name__ == "__main__":
    debug_order()
