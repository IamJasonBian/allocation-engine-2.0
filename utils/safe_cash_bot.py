"""
Safe Cash-Only Trading Bot for Account 919433888
ISOLATED: Only trades with cash in the specified account
"""

import os
import sys
from datetime import datetime

import robin_stocks.robinhood as r
from dotenv import load_dotenv

from .rh_auth import RobinhoodAuth


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
            # Check if running in interactive mode
            if sys.stdin.isatty():
                response = input("Continue anyway? (yes/no): ")
                if response.lower() != 'yes':
                    sys.exit(1)
            else:
                print("   Non-interactive mode: proceeding with configured account")

        self.auth = RobinhoodAuth()
        self.auth.login()

        # Verify account access
        self._verify_account()

    def _verify_account(self):
        """Verify we can access the correct account"""
        try:
            _account = r.profiles.load_account_profile(account_number=self.account_number)
            account_type = _account.get('type', 'unknown')

            print(f"\n{'='*70}")
            print(f"🔒 LOCKED TO ACCOUNT: {self.account_number}")
            print(f"{'='*70}")
            print(f"   Account Type: {account_type}")

            if account_type != 'cash':
                print(f"   ⚠️  WARNING: Account type is '{account_type}', not 'cash'")
                print("   Bot will still only use available cash, not margin")

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
            r.profiles.load_account_profile(account_number=self.account_number)
            portfolio = r.profiles.load_portfolio_profile(account_number=self.account_number)

            print(f"\n{'='*70}")
            print(f"💼 PORTFOLIO SUMMARY - ACCOUNT {self.account_number}")
            print(f"{'='*70}\n")

            # Account details
            print("💰 Cash Balances:")
            cash_info = self.get_cash_balance()
            print(f"   Available Cash: ${cash_info['tradeable_cash']:,.2f}")
            print(f"   Buying Power: ${cash_info['buying_power']:,.2f}")
            print(f"   Withdrawable: ${cash_info['cash_available_for_withdrawal']:,.2f}")

            # Portfolio value
            equity = float(portfolio.get('equity', 0))
            market_value = float(portfolio.get('market_value', 0))

            print("\n📊 Portfolio:")
            print(f"   Total Equity: ${equity:,.2f}")
            print(f"   Market Value: ${market_value:,.2f}")

            # Margin Availability Summary
            print("\n💳 Margin Availability:")
            available_cash = cash_info['tradeable_cash']
            buying_power = cash_info['buying_power']
            margin_available = buying_power - available_cash

            # Calculate margin usage if we have positions
            if equity > 0:
                cash_pct = (available_cash / equity) * 100
                margin_used = equity - available_cash - market_value
                print(f"   Current Margin Used: ${margin_used:,.2f}")
                print(f"   Margin Available: ${margin_available:,.2f}")
                print(f"   Cash % of Equity: {cash_pct:.1f}%")
                print(f"   Status: {'✅ Cash Only' if margin_used <= 0 else '⚠️ Using Margin'}")
            else:
                print(f"   Margin Available: ${margin_available:,.2f}")
                print(f"   Status: ✅ Cash Only (No positions)")

            # Order Book
            open_orders = self.get_open_orders()
            print(f"\n📋 Order Book: {len(open_orders)} open order(s)")

            if open_orders:
                # Group orders by type
                buy_orders = [o for o in open_orders if o['side'] == 'BUY']
                sell_orders = [o for o in open_orders if o['side'] == 'SELL']

                if buy_orders:
                    print("\n   🟢 BUY ORDERS:")
                    for order in buy_orders:
                        print(f"\n      {order['symbol']} - {order['order_type']}")
                        print(f"         Quantity: {order['quantity']:.0f} shares")
                        if order['limit_price']:
                            print(f"         Limit Price: ${order['limit_price']:.2f}")
                        if order['stop_price']:
                            print(f"         Stop Price: ${order['stop_price']:.2f}")
                        print(f"         Status: {order['state']}")
                        print(f"         Created: {order['created_at']}")

                if sell_orders:
                    print("\n   🔴 SELL ORDERS:")
                    for order in sell_orders:
                        print(f"\n      {order['symbol']} - {order['order_type']}")
                        print(f"         Quantity: {order['quantity']:.0f} shares")
                        if order['limit_price']:
                            print(f"         Limit Price: ${order['limit_price']:.2f}")
                        if order['stop_price']:
                            print(f"         Stop Price: ${order['stop_price']:.2f}")
                        print(f"         Status: {order['state']}")
                        print(f"         Created: {order['created_at']}")
            else:
                print("   No open orders")

            # Positions
            positions = self.get_positions()
            print(f"\n📈 Positions: {len(positions)}")

            if positions:
                # Calculate total position value for allocation percentages
                total_position_value = sum(pos['equity'] for pos in positions)

                for pos in positions:
                    allocation_pct = (pos['equity'] / equity) * 100 if equity > 0 else 0
                    print(f"\n   {pos['symbol']}")
                    print(f"      Quantity: {pos['quantity']}")
                    print(f"      Avg Buy: ${pos['avg_buy_price']:.2f}")
                    print(f"      Current: ${pos['current_price']:.2f}")
                    print(f"      Equity: ${pos['equity']:,.2f}")
                    print(f"      Allocation: {allocation_pct:.1f}% of portfolio")
                    print(f"      P/L: ${pos['profit_loss']:+,.2f} ({pos['profit_loss_pct']:+.2f}%)")

                # Stock Distribution (within invested portion)
                print(f"\n📊 Stock Distribution (of invested capital):")
                for pos in positions:
                    stock_pct = (pos['equity'] / total_position_value) * 100 if total_position_value > 0 else 0
                    print(f"   {pos['symbol']}: {stock_pct:.1f}% (${pos['equity']:,.2f})")

                # Portfolio Allocation Summary
                print(f"\n📊 Portfolio Allocation Summary:")
                cash_allocation_pct = (available_cash / equity) * 100 if equity > 0 else 100
                invested_pct = (total_position_value / equity) * 100 if equity > 0 else 0
                print(f"   💵 Cash: {cash_allocation_pct:.1f}% (${available_cash:,.2f})")
                print(f"   📈 Invested: {invested_pct:.1f}% (${total_position_value:,.2f})")
                print(f"   📊 Total Equity: ${equity:,.2f}")
            else:
                print("   No open positions")
                print(f"\n📊 Portfolio Allocation Summary:")
                print(f"   💵 Cash: 100.0% (${available_cash:,.2f})")
                print(f"   📈 Invested: 0.0% ($0.00)")

            print(f"\n{'='*70}\n")

            return {
                'cash': cash_info,
                'equity': equity,
                'market_value': market_value,
                'positions': positions,
                'open_orders': open_orders
            }

        except Exception as e:
            print(f"❌ Error getting portfolio: {e}")
            return None

    def get_positions(self):
        """Get positions for this specific account"""
        try:
            # Use build_holdings which works more reliably
            # Note: This returns all holdings, but we're locked to one account anyway
            holdings = r.account.build_holdings()

            positions = []
            if holdings:
                for symbol, data in holdings.items():
                    quantity = float(data.get('quantity', 0))
                    if quantity > 0:  # Only open positions
                        avg_price = float(data.get('average_buy_price', 0))
                        current_price = float(data.get('price', 0))
                        equity = float(data.get('equity', 0))
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

    def get_open_orders(self):
        """
        Get all open orders for this account

        Returns:
            List of open orders with details including stop loss and limit prices
        """
        try:
            # Get all open stock orders
            open_orders = r.orders.get_all_open_stock_orders()

            orders = []
            if open_orders:
                for order in open_orders:
                    # Parse order details
                    order_id = order.get('id', 'N/A')
                    symbol = order.get('symbol', 'N/A')

                    # If symbol is not directly available, try to resolve from instrument_id
                    if symbol == 'N/A':
                        instrument_id = order.get('instrument_id')
                        if instrument_id:
                            try:
                                instrument = r.stocks.get_instrument_by_url(
                                    f"https://api.robinhood.com/instruments/{instrument_id}/"
                                )
                                if instrument:
                                    symbol = instrument.get('symbol', 'N/A')
                            except Exception:
                                pass  # Keep symbol as 'N/A' if lookup fails

                    side = order.get('side', 'N/A')  # 'buy' or 'sell'
                    order_type = order.get('type', 'N/A')  # 'market' or 'limit'
                    trigger = order.get('trigger', 'immediate')  # 'immediate' or 'stop'
                    state = order.get('state', 'N/A')
                    quantity = float(order.get('quantity', 0))

                    # Price information
                    limit_price = order.get('price')  # Limit price (can be None)
                    stop_price = order.get('stop_price')  # Stop price (can be None)

                    # Timestamps
                    created_at = order.get('created_at', 'N/A')
                    updated_at = order.get('updated_at', 'N/A')

                    # Parse datetime if available
                    try:
                        if created_at != 'N/A':
                            created_dt = datetime.fromisoformat(created_at.replace('Z', '+00:00'))
                            created_at = created_dt.strftime('%Y-%m-%d %H:%M:%S')
                    except Exception:
                        pass

                    # Determine order description
                    if trigger == 'stop' and order_type == 'limit':
                        order_desc = 'Stop Limit'
                    elif trigger == 'stop':
                        order_desc = 'Stop Loss'
                    elif order_type == 'limit':
                        order_desc = 'Limit'
                    else:
                        order_desc = 'Market'

                    orders.append({
                        'order_id': order_id,
                        'symbol': symbol,
                        'side': side.upper() if side != 'N/A' else 'N/A',
                        'order_type': order_desc,
                        'trigger': trigger,
                        'state': state,
                        'quantity': quantity,
                        'limit_price': float(limit_price) if limit_price else None,
                        'stop_price': float(stop_price) if stop_price else None,
                        'created_at': created_at,
                        'updated_at': updated_at
                    })

            return orders

        except Exception as e:
            print(f"❌ Error getting open orders: {e}")
            return []

    def validate_buy_order(self, symbol, quantity, price):
        """
        Validate a buy order before execution
        Returns: (is_valid, reason)
        """
        cash_info = self.get_cash_balance()
        if not cash_info:
            return False, "Cannot retrieve cash balance"

        buying_power = cash_info['buying_power']
        total_cost = quantity * price

        # Add 1% buffer for price fluctuations
        total_cost_with_buffer = total_cost * 1.01

        if total_cost_with_buffer > buying_power:
            return False, f"Insufficient buying power: need ${total_cost_with_buffer:,.2f}, have ${buying_power:,.2f}"

        # Check if symbol is valid
        try:
            quote = r.stocks.get_quotes(symbol)
            if not quote or len(quote) == 0:
                return False, f"Invalid symbol: {symbol}"
        except Exception:
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
            print("\n⚠️  DRY RUN MODE - Order not executed")
            print("   To execute real orders, call with dry_run=False")
            print(f"{'='*70}\n")
            return None

        # Execute real order
        try:
            print("\n🚀 Executing order...")
            order = r.orders.order_buy_limit(
                symbol=symbol,
                quantity=quantity,
                limitPrice=price,
                account_number=self.account_number
            )

            print("✅ Order placed successfully!")
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

        print("   Validation: ✅ Valid sell order")

        if dry_run:
            print("\n⚠️  DRY RUN MODE - Order not executed")
            print("   To execute real orders, call with dry_run=False")
            print(f"{'='*70}\n")
            return None

        # Execute real order
        try:
            print("\n🚀 Executing order...")
            order = r.orders.order_sell_limit(
                symbol=symbol,
                quantity=quantity,
                limitPrice=price,
                account_number=self.account_number
            )

            print("✅ Order placed successfully!")
            print(f"   Order ID: {order.get('id', 'N/A')}")
            print(f"   State: {order.get('state', 'N/A')}")
            print(f"{'='*70}\n")

            return order

        except Exception as e:
            print(f"❌ Order failed: {e}")
            print(f"{'='*70}\n")
            return None

    def place_stop_limit_sell_order(self, symbol, quantity, stop_price, limit_price, dry_run=True):
        """
        Place a stop-limit sell order

        Args:
            symbol: Stock ticker
            quantity: Number of shares
            stop_price: Price that triggers the order
            limit_price: Minimum price to accept once triggered
            dry_run: If True, simulates order without execution
        """
        print(f"\n{'='*70}")
        print(f"STOP-LIMIT SELL ORDER - {'DRY RUN' if dry_run else 'LIVE'}")
        print(f"{'='*70}")

        positions = self.get_positions()
        position = next((p for p in positions if p['symbol'] == symbol), None)

        print(f"   Account: {self.account_number}")
        print(f"   Symbol: {symbol}")
        print(f"   Quantity: {quantity}")
        print(f"   Stop Price: ${stop_price:.2f}")
        print(f"   Limit Price: ${limit_price:.2f}")
        print(f"   Total Value: ${quantity * limit_price:.2f}")

        if not position:
            print(f"   Validation: No position in {symbol}")
            print(f"\nOrder rejected: You don't own {symbol}")
            print(f"{'='*70}\n")
            return None

        if quantity > position['quantity']:
            print(f"   Validation: Insufficient shares (have {position['quantity']})")
            print(f"\nOrder rejected: Can't sell {quantity} shares, only own {position['quantity']}")
            print(f"{'='*70}\n")
            return None

        print("   Validation: Valid stop-limit sell order")

        if dry_run:
            print("\n   DRY RUN MODE - Order not executed")
            print(f"{'='*70}\n")
            return None

        try:
            print("\n   Executing order...")
            order = r.orders.order_sell_stop_limit(
                symbol=symbol,
                quantity=quantity,
                limitPrice=limit_price,
                stopPrice=stop_price,
                account_number=self.account_number,
                timeInForce='gtc'
            )

            print("   Order placed successfully!")
            print(f"   Order ID: {order.get('id', 'N/A')}")
            print(f"   State: {order.get('state', 'N/A')}")
            print(f"{'='*70}\n")

            return order

        except Exception as e:
            print(f"   Order failed: {e}")
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
    print("\n🤖 Safe Cash-Only Trading Bot")
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
