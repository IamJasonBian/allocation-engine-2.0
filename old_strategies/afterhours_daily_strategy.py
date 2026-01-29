"""
After-Hours Daily Trading Strategy for AAPL
Strategy: One buy at market close, one sell next morning
Account: 490706777 (Cash Only - $2,000)

Schedule:
- 4:00 PM ET: Buy AAPL at close price
- 9:30 AM ET (next day): Sell AAPL at open price
"""

import sys
import os
import json
from datetime import datetime, time

import robin_stocks.robinhood as r
import pytz

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from utils.safe_cash_bot import SafeCashBot  # noqa: E402


class AfterHoursDailyStrategy:
    """
    Simple daily strategy:
    - Buy 1 share AAPL at 4:00 PM (market close)
    - Sell 1 share AAPL at 9:30 AM next day (market open)
    - Capture overnight price movement
    """

    def __init__(self, symbol='AAPL', dry_run=True):
        self.symbol = symbol
        self.dry_run = dry_run
        self.bot = SafeCashBot()
        self.state_file = 'afterhours_state.json'

        print(f"\n{'='*70}")
        print("🌙 AFTER-HOURS DAILY STRATEGY")
        print(f"{'='*70}")
        print(f"   Symbol: {self.symbol}")
        print(f"   Mode: {'DRY RUN' if self.dry_run else '⚠️  LIVE TRADING'}")
        print("   Strategy: Buy at close, sell at open")
        print(f"{'='*70}\n")

    def load_state(self):
        """Load strategy state (track if we bought today)"""
        if os.path.exists(self.state_file):
            with open(self.state_file, 'r') as f:
                return json.load(f)
        return {
            'last_buy_date': None,
            'last_sell_date': None,
            'position_held': False,
            'buy_price': None,
            'buy_quantity': None
        }

    def save_state(self, state):
        """Save strategy state"""
        with open(self.state_file, 'w') as f:
            json.dump(state, f, indent=2)
        print(f"💾 State saved: {state}")

    def get_current_time(self):
        """Get current time in ET timezone"""
        et_tz = pytz.timezone('US/Eastern')
        return datetime.now(et_tz)

    def is_market_hours(self):
        """Check if market is open (9:30 AM - 4:00 PM ET)"""
        current_time = self.get_current_time()

        # Check if weekday
        if current_time.weekday() >= 5:  # Saturday=5, Sunday=6
            return False, "Weekend"

        market_open = time(9, 30)
        market_close = time(16, 0)
        current_time_only = current_time.time()

        if current_time_only < market_open:
            return False, "Before market open"
        elif current_time_only >= market_close:
            return False, "After market close"
        else:
            return True, "Market hours"

    def get_current_position(self):
        """Check if we have AAPL position"""
        positions = self.bot.get_positions()
        position = next((p for p in positions if p['symbol'] == self.symbol), None)
        return position

    def calculate_position_size(self):
        """Calculate how many shares to buy with available cash"""
        cash_info = self.bot.get_cash_balance()
        available_cash = cash_info['tradeable_cash']

        # Get current price
        current_price = float(r.stocks.get_latest_price(self.symbol)[0])

        # Use 90% of available cash (conservative)
        max_investment = available_cash * 0.90
        quantity = int(max_investment / current_price)

        # Minimum 1 share
        if quantity < 1:
            return None, current_price, "Insufficient cash for 1 share"

        return quantity, current_price, f"Can buy {quantity} shares"

    def execute_buy(self):
        """Execute buy order at market close (4:00 PM)"""
        print(f"\n{'='*70}")
        print("🛒 BUY EXECUTION - Market Close")
        print(f"{'='*70}")

        state = self.load_state()
        current_date = self.get_current_time().date().isoformat()

        # Check if already bought today
        if state['last_buy_date'] == current_date:
            print(f"✋ Already bought today ({current_date})")
            print("   Waiting for sell signal tomorrow\n")
            return None

        # Check if we already have a position
        position = self.get_current_position()
        if position:
            print(f"✋ Already holding {position['quantity']} shares")
            print("   Waiting for sell signal\n")
            return None

        # Calculate position size
        quantity, current_price, message = self.calculate_position_size()
        print(f"💰 Position Sizing: {message}")

        if quantity is None:
            print(f"❌ Cannot buy: {message}\n")
            return None

        # Set limit price (current price + 0.5% for after-hours volatility)
        limit_price = current_price * 1.005

        print("\n📊 Order Details:")
        print(f"   Symbol: {self.symbol}")
        print(f"   Quantity: {quantity}")
        print(f"   Current Price: ${current_price:.2f}")
        print(f"   Limit Price: ${limit_price:.2f}")
        print(f"   Total: ${quantity * limit_price:.2f}")

        # Execute order
        order = self.bot.place_cash_buy_order(
            self.symbol,
            quantity,
            limit_price,
            dry_run=self.dry_run
        )

        # Update state
        if order or self.dry_run:
            state['last_buy_date'] = current_date
            state['position_held'] = True
            state['buy_price'] = current_price
            state['buy_quantity'] = quantity
            self.save_state(state)

        return order

    def execute_sell(self):
        """Execute sell order at market open (9:30 AM)"""
        print(f"\n{'='*70}")
        print("💵 SELL EXECUTION - Market Open")
        print(f"{'='*70}")

        state = self.load_state()
        current_date = self.get_current_time().date().isoformat()

        # Check if already sold today
        if state['last_sell_date'] == current_date:
            print(f"✋ Already sold today ({current_date})")
            print("   Waiting for buy signal at close\n")
            return None

        # Check if we have a position to sell
        position = self.get_current_position()
        if not position:
            print("✋ No position to sell")
            print("   Will buy at market close\n")
            return None

        quantity = int(position['quantity'])
        current_price = position['current_price']
        buy_price = state.get('buy_price', position['avg_buy_price'])

        # Calculate P/L
        profit_loss = (current_price - buy_price) * quantity
        profit_loss_pct = ((current_price - buy_price) / buy_price) * 100 if buy_price > 0 else 0

        # Set limit price (current price - 0.5%)
        limit_price = current_price * 0.995

        print("\n📊 Order Details:")
        print(f"   Symbol: {self.symbol}")
        print(f"   Quantity: {quantity}")
        print(f"   Buy Price: ${buy_price:.2f}")
        print(f"   Current Price: ${current_price:.2f}")
        print(f"   Limit Price: ${limit_price:.2f}")
        print(f"   P/L: ${profit_loss:+.2f} ({profit_loss_pct:+.2f}%)")

        # Execute order
        order = self.bot.place_sell_order(
            self.symbol,
            quantity,
            limit_price,
            dry_run=self.dry_run
        )

        # Update state
        if order or self.dry_run:
            state['last_sell_date'] = current_date
            state['position_held'] = False
            state['buy_price'] = None
            state['buy_quantity'] = None
            self.save_state(state)

        return order

    def run_strategy(self):
        """Run strategy based on time of day"""
        current_time = self.get_current_time()

        print(f"\n{'='*70}")
        print("🚀 RUNNING AFTER-HOURS DAILY STRATEGY")
        print(f"{'='*70}")
        print(f"   Time: {current_time.strftime('%Y-%m-%d %H:%M:%S %Z')}")
        print(f"   Day: {current_time.strftime('%A')}")

        is_market, market_status = self.is_market_hours()
        print(f"   Market Status: {market_status}")
        print(f"{'='*70}\n")

        # Load state
        state = self.load_state()
        print("📊 Current State:")
        print(f"   Position Held: {state['position_held']}")
        print(f"   Last Buy: {state['last_buy_date'] or 'Never'}")
        print(f"   Last Sell: {state['last_sell_date'] or 'Never'}")

        # Show portfolio
        self.bot.get_portfolio_summary()

        # Determine action based on time
        # Buy window: 3:55 PM - 4:15 PM (around market close)
        # Sell window: 9:25 AM - 9:45 AM (around market open)

        buy_window_start = time(15, 55)  # 3:55 PM
        buy_window_end = time(16, 15)    # 4:15 PM

        sell_window_start = time(9, 25)   # 9:25 AM
        sell_window_end = time(9, 45)     # 9:45 AM

        current_time_only = current_time.time()

        if sell_window_start <= current_time_only <= sell_window_end:
            print("\n⏰ SELL WINDOW (Market Open)")
            return self.execute_sell()

        elif buy_window_start <= current_time_only <= buy_window_end:
            print("\n⏰ BUY WINDOW (Market Close)")
            return self.execute_buy()

        else:
            print("\n⏸️  WAITING")
            print("   Next sell window: 9:25 AM - 9:45 AM ET")
            print("   Next buy window: 3:55 PM - 4:15 PM ET\n")
            return None

    def get_performance_summary(self):
        """Show historical performance"""
        print(f"\n{'='*70}")
        print("📊 STRATEGY PERFORMANCE")
        print(f"{'='*70}\n")

        state = self.load_state()

        print(f"Last Buy Date: {state['last_buy_date'] or 'N/A'}")
        print(f"Last Sell Date: {state['last_sell_date'] or 'N/A'}")
        print(f"Position Held: {state['position_held']}")

        if state['buy_price']:
            print(f"Buy Price: ${state['buy_price']:.2f}")
            print(f"Buy Quantity: {state['buy_quantity']}")

        print(f"\n{'='*70}\n")


def main():
    """Main execution"""
    import argparse

    parser = argparse.ArgumentParser(description='After-Hours Daily Trading Strategy')
    parser.add_argument('--live', action='store_true', help='Execute LIVE trades')
    parser.add_argument('--symbol', type=str, default='AAPL', help='Stock symbol')
    parser.add_argument('--performance', action='store_true', help='Show performance only')

    args = parser.parse_args()

    dry_run = not args.live

    # Warning for live mode
    if args.live:
        print("\n" + "="*70)
        print("⚠️  WARNING: LIVE TRADING MODE")
        print("="*70)
        print("Real orders will be executed!")
        print("Press Ctrl+C within 5 seconds to cancel...")
        print("="*70 + "\n")

        import time
        try:
            time.sleep(5)
        except KeyboardInterrupt:
            print("\n❌ Cancelled\n")
            return

    # Initialize strategy
    strategy = AfterHoursDailyStrategy(symbol=args.symbol, dry_run=dry_run)

    # Show performance or run strategy
    if args.performance:
        strategy.get_performance_summary()
    else:
        strategy.run_strategy()

    print("\n" + "="*70)
    print("💡 USAGE")
    print("="*70)
    if dry_run:
        print("✅ DRY RUN mode - No real orders executed")
        print("\nTo execute LIVE trades:")
        print("  python afterhours_daily_strategy.py --live")
    else:
        print("⚠️  LIVE mode - Real orders executed")

    print("\nSchedule this script:")
    print("  - 9:30 AM ET: Sell at market open")
    print("  - 4:00 PM ET: Buy at market close")
    print("\nCron job example:")
    print("  30 9 * * 1-5 cd ~/robinhood-trading && python3 afterhours_daily_strategy.py --live")
    print("  0 16 * * 1-5 cd ~/robinhood-trading && python3 afterhours_daily_strategy.py --live")
    print("="*70 + "\n")


if __name__ == "__main__":
    main()
