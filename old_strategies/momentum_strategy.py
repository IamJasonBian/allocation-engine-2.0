"""
Simple Momentum Trading Strategy for AAPL
Strategy: Buy when price momentum is positive, sell when negative
Account: 919433888 (Cash Only)
"""

import robin_stocks.robinhood as r
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from utils.safe_cash_bot import SafeCashBot
from datetime import datetime, timedelta
import time


class MomentumStrategy:
    """
    Simple momentum strategy based on moving averages

    Strategy Logic:
    - Calculate short-term (5-period) and long-term (20-period) moving averages
    - BUY signal: Short MA crosses above Long MA (bullish momentum)
    - SELL signal: Short MA crosses below Long MA (bearish momentum)
    - Position sizing: Use a fixed percentage of available cash
    """

    def __init__(self, symbol='AAPL', position_size_pct=0.10, dry_run=True):
        """
        Initialize momentum strategy

        Args:
            symbol: Stock ticker to trade
            position_size_pct: Percentage of cash to use per trade (0.10 = 10%)
            dry_run: If True, simulates trades without execution
        """
        self.symbol = symbol
        self.position_size_pct = position_size_pct
        self.dry_run = dry_run
        self.bot = SafeCashBot()

        print(f"\n{'='*70}")
        print(f"🎯 MOMENTUM STRATEGY INITIALIZED")
        print(f"{'='*70}")
        print(f"   Symbol: {self.symbol}")
        print(f"   Position Size: {self.position_size_pct * 100}% of cash")
        print(f"   Mode: {'DRY RUN' if self.dry_run else '⚠️  LIVE TRADING'}")
        print(f"{'='*70}\n")

    def get_historical_prices(self, interval='10minute', span='week'):
        """
        Get historical price data

        Args:
            interval: '5minute', '10minute', 'hour', 'day'
            span: 'day', 'week', 'month'

        Returns:
            List of closing prices
        """
        try:
            historicals = r.stocks.get_stock_historicals(
                self.symbol,
                interval=interval,
                span=span,
                bounds='regular'
            )

            if not historicals:
                print(f"❌ No historical data for {self.symbol}")
                return []

            # Extract closing prices
            prices = [float(h['close_price']) for h in historicals if h['close_price']]

            print(f"✅ Retrieved {len(prices)} price points for {self.symbol}")
            return prices

        except Exception as e:
            print(f"❌ Error fetching historical data: {e}")
            return []

    def calculate_moving_average(self, prices, period):
        """Calculate simple moving average"""
        if len(prices) < period:
            return None

        return sum(prices[-period:]) / period

    def calculate_momentum_signals(self, prices, short_period=5, long_period=20):
        """
        Calculate momentum signals based on moving averages

        Returns:
            dict with signal info
        """
        if len(prices) < long_period:
            return {
                'signal': 'HOLD',
                'reason': f'Not enough data (need {long_period} points, have {len(prices)})',
                'short_ma': None,
                'long_ma': None,
                'current_price': prices[-1] if prices else None
            }

        # Calculate moving averages
        short_ma = self.calculate_moving_average(prices, short_period)
        long_ma = self.calculate_moving_average(prices, long_period)

        # Previous moving averages (for crossover detection)
        prev_short_ma = self.calculate_moving_average(prices[:-1], short_period)
        prev_long_ma = self.calculate_moving_average(prices[:-1], long_period)

        current_price = prices[-1]

        # Determine signal
        signal = 'HOLD'
        reason = ''

        # Check for crossover
        if short_ma and long_ma and prev_short_ma and prev_long_ma:
            # Bullish crossover: short MA crosses above long MA
            if prev_short_ma <= prev_long_ma and short_ma > long_ma:
                signal = 'BUY'
                reason = f'Bullish crossover: Short MA ({short_ma:.2f}) crossed above Long MA ({long_ma:.2f})'

            # Bearish crossover: short MA crosses below long MA
            elif prev_short_ma >= prev_long_ma and short_ma < long_ma:
                signal = 'SELL'
                reason = f'Bearish crossover: Short MA ({short_ma:.2f}) crossed below Long MA ({long_ma:.2f})'

            # No crossover - check current position
            elif short_ma > long_ma:
                signal = 'HOLD'
                reason = f'Bullish trend: Short MA ({short_ma:.2f}) > Long MA ({long_ma:.2f}), holding position'
            else:
                signal = 'HOLD'
                reason = f'Bearish trend: Short MA ({short_ma:.2f}) < Long MA ({long_ma:.2f}), staying out'

        return {
            'signal': signal,
            'reason': reason,
            'short_ma': short_ma,
            'long_ma': long_ma,
            'current_price': current_price,
            'momentum': 'BULLISH' if short_ma and long_ma and short_ma > long_ma else 'BEARISH'
        }

    def get_current_position(self):
        """Get current position in the symbol"""
        positions = self.bot.get_positions()
        position = next((p for p in positions if p['symbol'] == self.symbol), None)
        return position

    def execute_signal(self, signal_info):
        """
        Execute trading signal

        Args:
            signal_info: Dict with signal information
        """
        signal = signal_info['signal']
        current_price = signal_info['current_price']

        print(f"\n{'='*70}")
        print(f"📊 SIGNAL ANALYSIS")
        print(f"{'='*70}")
        print(f"   Symbol: {self.symbol}")
        print(f"   Current Price: ${current_price:.2f}")
        print(f"   Short MA (5): ${signal_info['short_ma']:.2f}" if signal_info['short_ma'] else "   Short MA: N/A")
        print(f"   Long MA (20): ${signal_info['long_ma']:.2f}" if signal_info['long_ma'] else "   Long MA: N/A")
        print(f"   Momentum: {signal_info.get('momentum', 'N/A')}")
        print(f"   Signal: {signal}")
        print(f"   Reason: {signal_info['reason']}")
        print(f"{'='*70}\n")

        if signal == 'HOLD':
            print("✋ HOLD - No action taken\n")
            return None

        # Get current position
        position = self.get_current_position()

        if signal == 'BUY':
            # Check if we already have a position
            if position:
                print(f"⚠️  Already holding {position['quantity']} shares of {self.symbol}")
                print(f"   Skipping BUY signal\n")
                return None

            # Calculate position size
            cash_info = self.bot.get_cash_balance()
            available_cash = cash_info['tradeable_cash']
            position_value = available_cash * self.position_size_pct
            quantity = int(position_value / current_price)

            if quantity < 1:
                print(f"⚠️  Insufficient cash to buy 1 share")
                print(f"   Available: ${available_cash:.2f}")
                print(f"   Position size (10%): ${position_value:.2f}")
                print(f"   Share price: ${current_price:.2f}\n")
                return None

            # Place buy order at current price + 0.5% buffer
            limit_price = current_price * 1.005

            print(f"🛒 BUY SIGNAL EXECUTION")
            print(f"   Quantity: {quantity} shares")
            print(f"   Limit Price: ${limit_price:.2f}")
            print(f"   Total Cost: ${quantity * limit_price:.2f}")

            return self.bot.place_cash_buy_order(
                self.symbol,
                quantity,
                limit_price,
                dry_run=self.dry_run
            )

        elif signal == 'SELL':
            # Check if we have a position to sell
            if not position:
                print(f"⚠️  No position in {self.symbol} to sell\n")
                return None

            # Sell entire position
            quantity = int(position['quantity'])

            # Place sell order at current price - 0.5% buffer
            limit_price = current_price * 0.995

            print(f"💵 SELL SIGNAL EXECUTION")
            print(f"   Quantity: {quantity} shares")
            print(f"   Limit Price: ${limit_price:.2f}")
            print(f"   Total Value: ${quantity * limit_price:.2f}")

            return self.bot.place_sell_order(
                self.symbol,
                quantity,
                limit_price,
                dry_run=self.dry_run
            )

    def run_strategy(self, interval='10minute', span='week'):
        """
        Run the momentum strategy once

        Args:
            interval: Price data interval
            span: Historical data span
        """
        print(f"\n{'='*70}")
        print(f"🚀 RUNNING MOMENTUM STRATEGY")
        print(f"{'='*70}")
        print(f"   Time: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        print(f"   Symbol: {self.symbol}")
        print(f"   Interval: {interval}, Span: {span}")
        print(f"{'='*70}\n")

        # Get historical prices
        prices = self.get_historical_prices(interval=interval, span=span)

        if not prices:
            print("❌ Cannot run strategy without price data\n")
            return

        # Calculate signals
        signal_info = self.calculate_momentum_signals(prices)

        # Execute signal
        order = self.execute_signal(signal_info)

        # Show current portfolio
        print("\n" + "="*70)
        print("📊 CURRENT PORTFOLIO")
        print("="*70)
        self.bot.get_portfolio_summary()

        return order

    def backtest(self, interval='day', span='3month'):
        """
        Simple backtest of the strategy
        Shows what signals would have been generated
        """
        print(f"\n{'='*70}")
        print(f"📈 BACKTESTING MOMENTUM STRATEGY")
        print(f"{'='*70}")
        print(f"   Symbol: {self.symbol}")
        print(f"   Period: {span}")
        print(f"{'='*70}\n")

        # Get historical prices
        historicals = r.stocks.get_stock_historicals(
            self.symbol,
            interval=interval,
            span=span,
            bounds='regular'
        )

        if not historicals:
            print("❌ No historical data available\n")
            return

        prices = [float(h['close_price']) for h in historicals if h['close_price']]
        dates = [h['begins_at'][:10] for h in historicals if h['close_price']]

        print(f"Analyzing {len(prices)} trading days...\n")

        signals = []

        # Slide through historical data
        for i in range(20, len(prices)):
            price_window = prices[:i+1]
            signal_info = self.calculate_momentum_signals(price_window)

            if signal_info['signal'] != 'HOLD':
                signals.append({
                    'date': dates[i],
                    'signal': signal_info['signal'],
                    'price': signal_info['current_price'],
                    'short_ma': signal_info['short_ma'],
                    'long_ma': signal_info['long_ma']
                })

        # Display signals
        print(f"{'Date':<12} {'Signal':<6} {'Price':<10} {'Short MA':<10} {'Long MA':<10}")
        print("-" * 70)

        for sig in signals[-10:]:  # Show last 10 signals
            print(f"{sig['date']:<12} {sig['signal']:<6} ${sig['price']:<9.2f} ${sig['short_ma']:<9.2f} ${sig['long_ma']:<9.2f}")

        print(f"\nTotal signals generated: {len(signals)}")
        print(f"   BUY signals: {sum(1 for s in signals if s['signal'] == 'BUY')}")
        print(f"   SELL signals: {sum(1 for s in signals if s['signal'] == 'SELL')}")
        print()


def main():
    """Example usage"""
    import argparse

    # Parse command line arguments
    parser = argparse.ArgumentParser(description='AAPL Momentum Trading Strategy')
    parser.add_argument(
        '--live',
        action='store_true',
        help='Execute LIVE trades (default is dry run)'
    )
    parser.add_argument(
        '--symbol',
        type=str,
        default='AAPL',
        help='Stock symbol to trade (default: AAPL)'
    )
    parser.add_argument(
        '--position-size',
        type=float,
        default=0.10,
        help='Position size as percentage of cash (default: 0.10 = 10%%)'
    )
    parser.add_argument(
        '--skip-backtest',
        action='store_true',
        help='Skip backtest and only run live signal analysis'
    )

    args = parser.parse_args()

    # Determine if dry run or live
    dry_run = not args.live

    # Show warning for live trading
    if args.live:
        print("\n" + "="*70)
        print("⚠️  WARNING: LIVE TRADING MODE ENABLED")
        print("="*70)
        print("Real orders will be executed if signals are generated!")
        print("Press Ctrl+C within 5 seconds to cancel...")
        print("="*70 + "\n")
        import time
        try:
            time.sleep(5)
        except KeyboardInterrupt:
            print("\n❌ Cancelled by user\n")
            return

    # Initialize strategy
    strategy = MomentumStrategy(
        symbol=args.symbol,
        position_size_pct=args.position_size,
        dry_run=dry_run
    )

    # Run backtest first (unless skipped)
    if not args.skip_backtest:
        print("\n" + "="*70)
        print("STEP 1: BACKTESTING")
        print("="*70)
        strategy.backtest(interval='day', span='3month')

    # Run strategy once
    print("\n" + "="*70)
    print("STEP 2: LIVE SIGNAL ANALYSIS")
    print("="*70)
    strategy.run_strategy(interval='10minute', span='week')

    # Show next steps
    print("\n" + "="*70)
    print("💡 USAGE")
    print("="*70)
    if dry_run:
        print("✅ DRY RUN mode - No real orders were executed")
        print("\nTo execute LIVE trades:")
        print("  python momentum_strategy.py --live")
    else:
        print("⚠️  LIVE mode - Real orders were executed (if signals present)")
        print("\nTo run in DRY RUN mode:")
        print("  python momentum_strategy.py")

    print("\nOther options:")
    print("  --symbol MSFT              # Trade MSFT instead of AAPL")
    print("  --position-size 0.05       # Use 5% of cash per trade")
    print("  --skip-backtest            # Skip backtest, run signal only")
    print("  --help                     # Show all options")
    print("="*70 + "\n")


if __name__ == "__main__":
    main()
