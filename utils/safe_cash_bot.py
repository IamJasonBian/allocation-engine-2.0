"""
Safe Cash-Only Trading Bot for Account 919433888
ISOLATED: Only trades with cash in the specified account
"""

import robin_stocks.robinhood as r
from .rh_auth import RobinhoodAuth
from dotenv import load_dotenv
import os
import sys
from datetime import datetime


class SafeCashBot:
    """
    Trading bot with strict safety controls:
    - Only trades in specified account (919433888)
    - Only uses available cash (no margin)
    - Validates all orders before execution
    """

    def __init__(self):
        load_dotenv()

        # Lock to specific account
        self.account_number = os.getenv('RH_AUTOMATED_ACCOUNT_NUMBER')

        if not self.account_number:
            print("❌ ERROR: RH_AUTOMATED_ACCOUNT_NUMBER not set in .env")
            sys.exit(1)

        if self.account_number != "490706777":
            print(f"⚠️  WARNING: Expected account 490706777, got {self.account_number}")
            response = input("Continue anyway? (yes/no): ")
            if response.lower() != 'yes':
                sys.exit(1)

        self.auth = RobinhoodAuth()
        self.auth.login()

        # Verify account access
        self._verify_account()

    def _verify_account(self):
        """Verify we can access the correct account"""
        try:
            account = r.profiles.load_account_profile(account_number=self.account_number)
            account_type = account.get('type', 'unknown')

            print(f"\n{'='*70}")
            print(f"🔒 LOCKED TO ACCOUNT: {self.account_number}")
            print(f"{'='*70}")
            print(f"   Account Type: {account_type}")

            if account_type != 'cash':
                print(f"   ⚠️  WARNING: Account type is '{account_type}', not 'cash'")
                print(f"   Bot will still only use available cash, not margin")

            print(f"{'='*70}\n")

        except Exception as e:
            print(f"❌ ERROR: Cannot access account {self.account_number}")
            print(f"   {e}")
            sys.exit(1)

    def get_cash_balance(self):
        """Get available cash balance (not margin)"""
        try:
            account = r.profiles.load_account_profile(account_number=self.account_number)

            cash = float(account.get('cash', 0))
            cash_available_for_withdrawal = float(account.get('cash_available_for_withdrawal', 0))
            buying_power = float(account.get('buying_power', 0))

            # Use the most conservative value (actual cash, not buying power)
            available_cash = cash

            return {
                'cash': cash,
                'cash_available_for_withdrawal': cash_available_for_withdrawal,
                'buying_power': buying_power,
                'tradeable_cash': available_cash  # This is what we'll use
            }
        except Exception as e:
            print(f"❌ Error getting cash balance: {e}")
            return None

    def get_portfolio_summary(self):
        """Get portfolio summary for this specific account"""
        try:
            account = r.profiles.load_account_profile(account_number=self.account_number)
            portfolio = r.profiles.load_portfolio_profile(account_number=self.account_number)

            print(f"\n{'='*70}")
            print(f"💼 PORTFOLIO SUMMARY - ACCOUNT {self.account_number}")
            print(f"{'='*70}\n")

            # Account details
            print(f"💰 Cash Balances:")
            cash_info = self.get_cash_balance()
            print(f"   Available Cash: ${cash_info['tradeable_cash']:,.2f}")
            print(f"   Buying Power: ${cash_info['buying_power']:,.2f}")
            print(f"   Withdrawable: ${cash_info['cash_available_for_withdrawal']:,.2f}")

            # Portfolio value
            equity = float(portfolio.get('equity', 0))
            market_value = float(portfolio.get('market_value', 0))

            print(f"\n📊 Portfolio:")
            print(f"   Total Equity: ${equity:,.2f}")
            print(f"   Market Value: ${market_value:,.2f}")

            # Positions
            positions = self.get_positions()
            print(f"\n📈 Positions: {len(positions)}")

            if positions:
                for pos in positions:
                    print(f"\n   {pos['symbol']}")
                    print(f"      Quantity: {pos['quantity']}")
                    print(f"      Avg Buy: ${pos['avg_buy_price']:.2f}")
                    print(f"      Current: ${pos['current_price']:.2f}")
                    print(f"      Equity: ${pos['equity']:,.2f}")
                    print(f"      P/L: ${pos['profit_loss']:+,.2f} ({pos['profit_loss_pct']:+.2f}%)")
            else:
                print("   No open positions")

            print(f"\n{'='*70}\n")

            return {
                'cash': cash_info,
                'equity': equity,
                'market_value': market_value,
                'positions': positions
            }

        except Exception as e:
            print(f"❌ Error getting portfolio: {e}")
            return None

    def get_positions(self):
        """Get positions for this specific account"""
        try:
            # Get positions filtered by account number
            url = f'https://api.robinhood.com/positions/?account_number={self.account_number}'
            data = r.helper.request_get(url, dataType='pagination')

            positions = []
            for pos in data:
                quantity = float(pos.get('quantity', 0))
                if quantity > 0:  # Only open positions
                    symbol = r.get_symbol_by_url(pos['instrument'])
                    avg_price = float(pos.get('average_buy_price', 0))
                    current_price = float(r.get_latest_price(symbol)[0])
                    equity = quantity * current_price
                    profit_loss = (current_price - avg_price) * quantity
                    profit_loss_pct = ((current_price - avg_price) / avg_price) * 100 if avg_price > 0 else 0

                    positions.append({
                        'symbol': symbol,
                        'quantity': quantity,
                        'avg_buy_price': avg_price,
                        'current_price': current_price,
                        'equity': equity,
                        'profit_loss': profit_loss,
                        'profit_loss_pct': profit_loss_pct
                    })

            return positions

        except Exception as e:
            print(f"❌ Error getting positions: {e}")
            return []

    def validate_buy_order(self, symbol, quantity, price):
        """
        Validate a buy order before execution
        Returns: (is_valid, reason)
        """
        cash_info = self.get_cash_balance()
        if not cash_info:
            return False, "Cannot retrieve cash balance"

        available_cash = cash_info['tradeable_cash']
        total_cost = quantity * price

        # Add 1% buffer for price fluctuations
        total_cost_with_buffer = total_cost * 1.01

        if total_cost_with_buffer > available_cash:
            return False, f"Insufficient cash: need ${total_cost_with_buffer:,.2f}, have ${available_cash:,.2f}"

        # Check if symbol is valid
        try:
            quote = r.stocks.get_quotes(symbol)
            if not quote or len(quote) == 0:
                return False, f"Invalid symbol: {symbol}"
        except:
            return False, f"Cannot get quote for {symbol}"

        return True, "Order validated"

    def place_cash_buy_order(self, symbol, quantity, price, dry_run=True):
        """
        Place a limit buy order using CASH ONLY

        Args:
            symbol: Stock ticker
            quantity: Number of shares
            price: Limit price
            dry_run: If True, simulates order without execution
        """
        print(f"\n{'='*70}")
        print(f"🛒 BUY ORDER - {'DRY RUN' if dry_run else 'LIVE'}")
        print(f"{'='*70}")

        # Validate order
        is_valid, reason = self.validate_buy_order(symbol, quantity, price)

        print(f"   Account: {self.account_number}")
        print(f"   Symbol: {symbol}")
        print(f"   Quantity: {quantity}")
        print(f"   Limit Price: ${price:.2f}")
        print(f"   Total Cost: ${quantity * price:.2f}")
        print(f"   Validation: {'✅ ' + reason if is_valid else '❌ ' + reason}")

        if not is_valid:
            print(f"\n❌ Order rejected: {reason}")
            print(f"{'='*70}\n")
            return None

        if dry_run:
            print(f"\n⚠️  DRY RUN MODE - Order not executed")
            print(f"   To execute real orders, call with dry_run=False")
            print(f"{'='*70}\n")
            return None

        # Execute real order
        try:
            print(f"\n🚀 Executing order...")
            order = r.orders.order_buy_limit(
                symbol=symbol,
                quantity=quantity,
                limitPrice=price,
                account_number=self.account_number
            )

            print(f"✅ Order placed successfully!")
            print(f"   Order ID: {order.get('id', 'N/A')}")
            print(f"   State: {order.get('state', 'N/A')}")
            print(f"{'='*70}\n")

            return order

        except Exception as e:
            print(f"❌ Order failed: {e}")
            print(f"{'='*70}\n")
            return None

    def place_sell_order(self, symbol, quantity, price, dry_run=True):
        """
        Place a limit sell order

        Args:
            symbol: Stock ticker
            quantity: Number of shares
            price: Limit price
            dry_run: If True, simulates order without execution
        """
        print(f"\n{'='*70}")
        print(f"💵 SELL ORDER - {'DRY RUN' if dry_run else 'LIVE'}")
        print(f"{'='*70}")

        # Check if we have the position
        positions = self.get_positions()
        position = next((p for p in positions if p['symbol'] == symbol), None)

        print(f"   Account: {self.account_number}")
        print(f"   Symbol: {symbol}")
        print(f"   Quantity: {quantity}")
        print(f"   Limit Price: ${price:.2f}")
        print(f"   Total Value: ${quantity * price:.2f}")

        if not position:
            print(f"   Validation: ❌ No position in {symbol}")
            print(f"\n❌ Order rejected: You don't own {symbol}")
            print(f"{'='*70}\n")
            return None

        if quantity > position['quantity']:
            print(f"   Validation: ❌ Insufficient shares (have {position['quantity']})")
            print(f"\n❌ Order rejected: Can't sell {quantity} shares, only own {position['quantity']}")
            print(f"{'='*70}\n")
            return None

        print(f"   Validation: ✅ Valid sell order")

        if dry_run:
            print(f"\n⚠️  DRY RUN MODE - Order not executed")
            print(f"   To execute real orders, call with dry_run=False")
            print(f"{'='*70}\n")
            return None

        # Execute real order
        try:
            print(f"\n🚀 Executing order...")
            order = r.orders.order_sell_limit(
                symbol=symbol,
                quantity=quantity,
                limitPrice=price,
                account_number=self.account_number
            )

            print(f"✅ Order placed successfully!")
            print(f"   Order ID: {order.get('id', 'N/A')}")
            print(f"   State: {order.get('state', 'N/A')}")
            print(f"{'='*70}\n")

            return order

        except Exception as e:
            print(f"❌ Order failed: {e}")
            print(f"{'='*70}\n")
            return None

    def get_quote(self, symbol):
        """Get real-time quote"""
        try:
            quote = r.stocks.get_quotes(symbol)[0]
            price = float(r.stocks.get_latest_price(symbol)[0])

            print(f"\n📊 {symbol} Quote:")
            print(f"   Price: ${price:.2f}")
            print(f"   Bid: ${float(quote.get('bid_price', 0)):.2f}")
            print(f"   Ask: ${float(quote.get('ask_price', 0)):.2f}")
            if 'volume' in quote and quote['volume']:
                print(f"   Volume: {int(float(quote['volume'])):,}")

            return price
        except Exception as e:
            print(f"❌ Error fetching quote: {e}")
            return None

    def run_example(self):
        """Example usage of the bot"""
        try:
            # Show portfolio
            self.get_portfolio_summary()

            # Get quote
            self.get_quote('AAPL')

            # Example buy order (DRY RUN)
            print("\n" + "="*70)
            print("📋 EXAMPLE: Placing a dry run buy order")
            print("="*70)
            self.place_cash_buy_order('AAPL', 1, 150.00, dry_run=True)

            print("\n💡 To execute real orders:")
            print("   bot.place_cash_buy_order('AAPL', 1, 150.00, dry_run=False)")

        except Exception as e:
            print(f"❌ Error: {e}")
        finally:
            self.auth.logout()


def main():
    """Run the safe cash bot - just show portfolio"""
    print(f"\n🤖 Safe Cash-Only Trading Bot")
    print(f"⏰ {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")

    bot = SafeCashBot()

    try:
        # Just show portfolio for automated account
        bot.get_portfolio_summary()
    except Exception as e:
        print(f"❌ Error: {e}")
    finally:
        bot.auth.logout()


if __name__ == "__main__":
    main()
