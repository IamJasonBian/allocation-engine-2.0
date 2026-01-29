"""
Example Robinhood Trading Bot
Demonstrates how to use the RobinhoodAuth class
"""

import sys
import os
import time
from datetime import datetime

import robin_stocks.robinhood as r

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from utils.rh_auth import RobinhoodAuth  # noqa: E402


class TradingBot:
    """Simple trading bot example"""

    def __init__(self, account_name='automated'):
        self.auth = RobinhoodAuth()
        self.auth.login(account_name)
        self.account_name = account_name

    def get_portfolio_summary(self):
        """Display portfolio summary"""
        print(f"\n{'='*60}")
        print(f"📊 PORTFOLIO SUMMARY - {self.account_name.upper()} ACCOUNT")
        print(f"{'='*60}")

        # Account info
        info = self.auth.get_account_info()
        print("\n💰 Account Overview:")
        print(f"   Account Number: {info.get('account_number', 'N/A')}")
        print(f"   Total Equity: ${float(info.get('equity', 0)):,.2f}")
        print(f"   Market Value: ${float(info.get('market_value', 0)):,.2f}")
        print(f"   Buying Power: ${float(info.get('buying_power', 0)):,.2f}")

        # Current positions
        positions = r.get_open_stock_positions()
        print(f"\n📈 Current Positions: {len(positions)}")

        if positions:
            for pos in positions:
                symbol = r.get_symbol_by_url(pos['instrument'])
                quantity = float(pos['quantity'])
                avg_price = float(pos['average_buy_price'])
                current_price = float(r.get_latest_price(symbol)[0])
                equity = quantity * current_price
                profit_loss = (current_price - avg_price) * quantity
                profit_loss_pct = ((current_price - avg_price) / avg_price) * 100

                print(f"\n   {symbol}")
                print(f"      Quantity: {quantity}")
                print(f"      Avg Buy Price: ${avg_price:.2f}")
                print(f"      Current Price: ${current_price:.2f}")
                print(f"      Equity: ${equity:,.2f}")
                print(f"      P/L: ${profit_loss:+,.2f} ({profit_loss_pct:+.2f}%)")
        else:
            print("   No open positions")

        print(f"\n{'='*60}\n")

    def get_stock_quote(self, symbol):
        """Get real-time quote for a stock"""
        try:
            quote = r.get_quote(symbol)
            price = float(r.get_latest_price(symbol)[0])

            print(f"\n📊 {symbol} Quote:")
            print(f"   Price: ${price:.2f}")
            print(f"   Bid: ${float(quote['bid_price']):.2f}")
            print(f"   Ask: ${float(quote['ask_price']):.2f}")
            print(f"   Volume: {int(float(quote['volume'])):,}")
            print(f"   Previous Close: ${float(quote['previous_close']):.2f}")

            return price
        except Exception as e:
            print(f"❌ Error fetching quote for {symbol}: {e}")
            return None

    def place_limit_order(self, symbol, quantity, price, order_type='buy'):
        """
        Place a limit order (DRY RUN - not executed)

        Args:
            symbol: Stock ticker
            quantity: Number of shares
            price: Limit price
            order_type: 'buy' or 'sell'
        """
        print("\n🔔 DRY RUN - Limit Order:")
        print(f"   Type: {order_type.upper()}")
        print(f"   Symbol: {symbol}")
        print(f"   Quantity: {quantity}")
        print(f"   Limit Price: ${price:.2f}")
        print(f"   Total Value: ${quantity * price:.2f}")
        print("\n⚠️  This is a DRY RUN. Uncomment the code below to execute real orders.")

        # Uncomment to execute real orders
        # try:
        #     if order_type == 'buy':
        #         order = r.order_buy_limit(symbol, quantity, price)
        #     else:
        #         order = r.order_sell_limit(symbol, quantity, price)
        #     print(f"✅ Order placed: {order}")
        #     return order
        # except Exception as e:
        #     print(f"❌ Order failed: {e}")
        #     return None

    def watch_stock(self, symbol, duration_seconds=60):
        """Watch a stock's price for a specified duration"""
        print(f"\n👀 Watching {symbol} for {duration_seconds} seconds...")
        print("   Press Ctrl+C to stop\n")

        start_time = time.time()
        try:
            while time.time() - start_time < duration_seconds:
                price = float(r.get_latest_price(symbol)[0])
                timestamp = datetime.now().strftime("%H:%M:%S")
                print(f"[{timestamp}] {symbol}: ${price:.2f}")
                time.sleep(5)  # Update every 5 seconds
        except KeyboardInterrupt:
            print(f"\n⏹️  Stopped watching {symbol}")

    def run(self):
        """Main bot execution"""
        try:
            # Show portfolio
            self.get_portfolio_summary()

            # Example: Get quote for AAPL
            self.get_stock_quote('AAPL')

            # Example: Watch a stock (uncomment to use)
            # self.watch_stock('AAPL', duration_seconds=30)

            # Example: Place a dry run order
            # self.place_limit_order('AAPL', 1, 150.00, 'buy')

        except Exception as e:
            print(f"❌ Error: {e}")
        finally:
            # Always logout
            self.auth.logout()


def main():
    """Run the trading bot"""
    print("\n🤖 Starting Trading Bot...")
    print(f"⏰ Time: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")

    # Initialize and run bot with automated account
    bot = TradingBot(account_name='automated')
    bot.run()


if __name__ == "__main__":
    main()
