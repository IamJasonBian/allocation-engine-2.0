"""
Place a single order with the trading bot
Account: 490706777 (Cash Only - $2,000)
"""

import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from utils.safe_cash_bot import SafeCashBot  # noqa: E402


def place_single_order():
    """Interactive script to place a single order"""

    print("\n" + "="*70)
    print("🛒 PLACE SINGLE ORDER - Account 490706777")
    print("="*70 + "\n")

    # Initialize bot
    bot = SafeCashBot()

    # Show portfolio
    bot.get_portfolio_summary()

    print("\n" + "="*70)
    print("📝 ORDER DETAILS")
    print("="*70 + "\n")

    # Get order details from user
    symbol = input("Enter stock symbol (e.g., AAPL): ").strip().upper()

    if not symbol:
        print("❌ No symbol entered. Exiting.")
        bot.auth.logout()
        return

    # Get current quote
    print(f"\nFetching current price for {symbol}...")
    current_price = bot.get_quote(symbol)

    if not current_price:
        print(f"❌ Could not get quote for {symbol}")
        bot.auth.logout()
        return

    print(f"\nCurrent price: ${current_price:.2f}")

    # Order type
    print("\nOrder type:")
    print("  1. BUY")
    print("  2. SELL")
    order_type = input("Select (1 or 2): ").strip()

    if order_type not in ['1', '2']:
        print("❌ Invalid selection. Exiting.")
        bot.auth.logout()
        return

    is_buy = order_type == '1'

    # Quantity
    quantity_str = input("\nEnter quantity (number of shares): ").strip()
    try:
        quantity = int(quantity_str)
        if quantity <= 0:
            print("❌ Quantity must be positive")
            bot.auth.logout()
            return
    except ValueError:
        print("❌ Invalid quantity")
        bot.auth.logout()
        return

    # Limit price
    default_price = current_price * 1.005 if is_buy else current_price * 0.995
    price_str = input(f"\nEnter limit price (press Enter for ${default_price:.2f}): ").strip()

    if price_str:
        try:
            price = float(price_str)
            if price <= 0:
                print("❌ Price must be positive")
                bot.auth.logout()
                return
        except ValueError:
            print("❌ Invalid price")
            bot.auth.logout()
            return
    else:
        price = default_price

    # Dry run or live
    print("\n" + "="*70)
    print("⚠️  EXECUTION MODE")
    print("="*70)
    print("\n1. DRY RUN (simulate order)")
    print("2. LIVE (execute real order)")

    mode = input("\nSelect mode (1 or 2): ").strip()

    if mode not in ['1', '2']:
        print("❌ Invalid selection. Exiting.")
        bot.auth.logout()
        return

    dry_run = mode == '1'

    # Confirm order
    print("\n" + "="*70)
    print("📋 ORDER SUMMARY")
    print("="*70)
    print("   Account: 490706777")
    print(f"   Type: {'BUY' if is_buy else 'SELL'}")
    print(f"   Symbol: {symbol}")
    print(f"   Quantity: {quantity}")
    print(f"   Limit Price: ${price:.2f}")
    print(f"   Total: ${quantity * price:.2f}")
    print(f"   Mode: {'DRY RUN' if dry_run else '⚠️  LIVE'}")
    print("="*70 + "\n")

    confirm = input("Confirm order? (yes/no): ").strip().lower()

    if confirm != 'yes':
        print("\n❌ Order cancelled by user\n")
        bot.auth.logout()
        return

    # Execute order
    if is_buy:
        order = bot.place_cash_buy_order(symbol, quantity, price, dry_run=dry_run)
    else:
        order = bot.place_sell_order(symbol, quantity, price, dry_run=dry_run)

    # Show result
    if order:
        print("\n" + "="*70)
        print("✅ ORDER SUBMITTED")
        print("="*70)
        print(f"   Order ID: {order.get('id', 'N/A')}")
        print(f"   State: {order.get('state', 'N/A')}")
        print("="*70 + "\n")
    elif dry_run:
        print("\n✅ Dry run completed - no real order placed\n")
    else:
        print("\n❌ Order failed - see error above\n")

    # Logout
    bot.auth.logout()


def quick_order(symbol, quantity, price=None, order_type='buy', dry_run=True):
    """
    Quick order function for scripting

    Example:
        quick_order('AAPL', 1, 250.00, 'buy', dry_run=True)
    """
    bot = SafeCashBot()

    # Get current price if not provided
    if price is None:
        current_price = bot.get_quote(symbol)
        if not current_price:
            print(f"❌ Could not get price for {symbol}")
            bot.auth.logout()
            return None

        # Set price with 0.5% buffer
        price = current_price * 1.005 if order_type == 'buy' else current_price * 0.995

    print(f"\n{'='*70}")
    print("📝 QUICK ORDER")
    print(f"{'='*70}")
    print(f"   Type: {order_type.upper()}")
    print(f"   Symbol: {symbol}")
    print(f"   Quantity: {quantity}")
    print(f"   Limit Price: ${price:.2f}")
    print(f"   Mode: {'DRY RUN' if dry_run else '⚠️  LIVE'}")
    print(f"{'='*70}\n")

    # Execute
    if order_type.lower() == 'buy':
        order = bot.place_cash_buy_order(symbol, quantity, price, dry_run=dry_run)
    else:
        order = bot.place_sell_order(symbol, quantity, price, dry_run=dry_run)

    bot.auth.logout()
    return order


if __name__ == "__main__":
    # Check if quick mode
    if len(sys.argv) > 1:
        # Quick mode: python place_single_order.py AAPL 1 250.00 buy --live
        try:
            symbol = sys.argv[1].upper()
            quantity = int(sys.argv[2])
            price = float(sys.argv[3]) if len(sys.argv) > 3 else None
            order_type = sys.argv[4] if len(sys.argv) > 4 else 'buy'
            dry_run = '--live' not in sys.argv

            quick_order(symbol, quantity, price, order_type, dry_run)
        except Exception as e:
            print(f"❌ Error: {e}")
            print("\nUsage:")
            print("  python place_single_order.py AAPL 1 250.00 buy --live")
            print("  python place_single_order.py MSFT 2 400.00 sell")
    else:
        # Interactive mode
        place_single_order()
