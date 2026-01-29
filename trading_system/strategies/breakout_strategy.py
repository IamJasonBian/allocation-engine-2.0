"""
30-Day Breakout Trading Strategy
Buy at 30-day low, Sell at 30-day high
"""

from typing import Dict, Optional, List


class BreakoutStrategy:
    """
    30-Day High/Low Breakout Strategy

    Strategy Logic:
    - Track 30-day high and low for each symbol
    - Queue BUY order when price hits 30-day low
    - Queue SELL order when price hits 30-day high
    - Use market orders for execution
    """

    def __init__(self, symbols: List[str], position_size_pct: float = 0.25):
        """
        Initialize breakout strategy

        Args:
            symbols: List of symbols to trade (BTC, SPY, QQQ, AMZN)
            position_size_pct: Percentage of portfolio per position (default 25%)
        """
        self.symbols = symbols
        self.position_size_pct = position_size_pct

        print(f"\n{'='*70}")
        print("30-DAY BREAKOUT STRATEGY INITIALIZED")
        print(f"{'='*70}")
        print(f"Symbols: {', '.join(symbols)}")
        print(f"Position Size: {position_size_pct * 100}% per symbol")
        print(f"{'='*70}\n")

    def analyze_symbol(self, symbol: str, metrics: Dict,
                       current_position: Optional[Dict] = None) -> Dict:
        """
        Analyze a symbol and generate trading signal

        Args:
            symbol: Stock symbol
            metrics: Current metrics (price, 30d high/low, etc.)
            current_position: Current position info (if any)

        Returns:
            Dictionary with signal and order details
        """
        current_price = metrics.get('current_price', 0)
        high_30d = metrics.get('30d_high', 0)
        low_30d = metrics.get('30d_low', 0)

        if not all([current_price, high_30d, low_30d]):
            return {
                'signal': 'NO_DATA',
                'reason': 'Insufficient metrics data',
                'order': None
            }

        # Define breakout thresholds (allow small tolerance)
        buy_threshold = low_30d * 1.001  # 0.1% above 30d low
        sell_threshold = high_30d * 0.999  # 0.1% below 30d high

        # Check if we already have a position
        has_position = current_position is not None and float(current_position.get('quantity', 0)) > 0

        # Generate signals
        if current_price <= buy_threshold and not has_position:
            return self._generate_buy_signal(symbol, current_price, low_30d, metrics)

        elif current_price >= sell_threshold and has_position:
            return self._generate_sell_signal(symbol, current_price, high_30d,
                                              metrics, current_position)

        else:
            # HOLD - no action
            return {
                'signal': 'HOLD',
                'reason': self._get_hold_reason(current_price, low_30d, high_30d, has_position),
                'order': None
            }

    def _generate_buy_signal(self, symbol: str, current_price: float,
                             low_30d: float, metrics: Dict) -> Dict:
        """Generate buy signal when price hits 30-day low"""
        return {
            'signal': 'BUY_AT_LOW',
            'reason': f'Price ${current_price:.2f} at 30-day low ${low_30d:.2f}',
            'order': {
                'action': 'buy',
                'symbol': symbol,
                'order_type': 'market',
                'trigger_price': low_30d,
                'current_price': current_price,
                'metrics': metrics
            }
        }

    def _generate_sell_signal(self, symbol: str, current_price: float,
                              high_30d: float, metrics: Dict,
                              position: Dict) -> Dict:
        """Generate sell signal when price hits 30-day high"""
        quantity = float(position.get('quantity', 0))

        return {
            'signal': 'SELL_AT_HIGH',
            'reason': f'Price ${current_price:.2f} at 30-day high ${high_30d:.2f}',
            'order': {
                'action': 'sell',
                'symbol': symbol,
                'order_type': 'market',
                'quantity': quantity,
                'trigger_price': high_30d,
                'current_price': current_price,
                'metrics': metrics
            }
        }

    def _get_hold_reason(self, price: float, low: float, high: float,
                         has_position: bool) -> str:
        """Get reason for HOLD signal"""
        if has_position:
            distance_to_high = ((high - price) / price) * 100
            return (f'Holding position. Price ${price:.2f} is {distance_to_high:.1f}% '
                    f'below 30d high ${high:.2f}')
        else:
            distance_to_low = ((price - low) / low) * 100
            return (f'No position. Price ${price:.2f} is {distance_to_low:.1f}% '
                    f'above 30d low ${low:.2f}')

    def calculate_position_size(self, symbol: str, price: float,
                                available_cash: float) -> int:
        """
        Calculate position size based on available cash

        Args:
            symbol: Stock symbol
            price: Current price
            available_cash: Available cash in account

        Returns:
            Number of shares/units to buy
        """
        position_value = available_cash * self.position_size_pct
        quantity = position_value / price

        # For crypto (BTC), allow fractional shares
        if symbol == 'BTC':
            return round(quantity, 4)  # 4 decimal places for BTC
        else:
            return int(quantity)  # Whole shares for stocks

    def format_signal(self, symbol: str, signal_data: Dict) -> str:
        """
        Format signal for display

        Args:
            symbol: Stock symbol
            signal_data: Signal data dictionary

        Returns:
            Formatted string
        """
        lines = [
            f"\n{'='*70}",
            f"SIGNAL ANALYSIS: {symbol}",
            f"{'='*70}",
            f"Signal: {signal_data['signal']}",
            f"Reason: {signal_data['reason']}",
        ]

        if signal_data['order']:
            order = signal_data['order']
            lines.extend([
                "",
                "Order Details:",
                f"  Action: {order['action'].upper()}",
                f"  Type: {order['order_type']}",
                f"  Current Price: ${order['current_price']:,.2f}",
                f"  Trigger Price: ${order['trigger_price']:,.2f}",
            ])

            if 'quantity' in order:
                lines.append(f"  Quantity: {order['quantity']}")

        lines.append(f"{'='*70}\n")

        return '\n'.join(lines)

    def backtest_signal_history(self, symbol: str, historical_data: List[Dict]) -> Dict:
        """
        Backtest strategy on historical data

        Args:
            symbol: Stock symbol
            historical_data: List of daily price data with 30d high/low calculated

        Returns:
            Backtest results
        """
        signals = []
        position = None

        for data in historical_data:
            # Simulate position
            current_position = {'quantity': 1} if position == 'long' else None

            # Analyze
            signal = self.analyze_symbol(
                symbol,
                {
                    'current_price': data['close'],
                    '30d_high': data.get('30d_high', 0),
                    '30d_low': data.get('30d_low', 0)
                },
                current_position
            )

            if signal['signal'] == 'BUY_AT_LOW':
                signals.append({
                    'date': data['date'],
                    'signal': 'BUY',
                    'price': data['close']
                })
                position = 'long'

            elif signal['signal'] == 'SELL_AT_HIGH':
                signals.append({
                    'date': data['date'],
                    'signal': 'SELL',
                    'price': data['close']
                })
                position = None

        return {
            'symbol': symbol,
            'total_signals': len(signals),
            'buy_signals': sum(1 for s in signals if s['signal'] == 'BUY'),
            'sell_signals': sum(1 for s in signals if s['signal'] == 'SELL'),
            'signals': signals
        }


def test_breakout_strategy():
    """Test breakout strategy"""
    print("Testing Breakout Strategy")
    print("=" * 70)

    # Initialize strategy
    strategy = BreakoutStrategy(
        symbols=['BTC', 'SPY', 'QQQ', 'AMZN'],
        position_size_pct=0.25
    )

    # Test scenario 1: Price at 30-day low (BUY signal)
    print("\n1. Testing BUY signal (price at 30-day low):")
    metrics_buy = {
        'current_price': 38000.00,
        '30d_high': 45000.00,
        '30d_low': 38000.00,
        'intraday_volatility': 2.5
    }
    signal_buy = strategy.analyze_symbol('BTC', metrics_buy, current_position=None)
    print(strategy.format_signal('BTC', signal_buy))

    # Test scenario 2: Price at 30-day high (SELL signal)
    print("\n2. Testing SELL signal (price at 30-day high):")
    metrics_sell = {
        'current_price': 45000.00,
        '30d_high': 45000.00,
        '30d_low': 38000.00,
        'intraday_volatility': 2.5
    }
    position = {'quantity': 0.1}
    signal_sell = strategy.analyze_symbol('BTC', metrics_sell, current_position=position)
    print(strategy.format_signal('BTC', signal_sell))

    # Test scenario 3: Price in middle range (HOLD signal)
    print("\n3. Testing HOLD signal (price in middle range):")
    metrics_hold = {
        'current_price': 42000.00,
        '30d_high': 45000.00,
        '30d_low': 38000.00,
        'intraday_volatility': 2.5
    }
    signal_hold = strategy.analyze_symbol('BTC', metrics_hold, current_position=None)
    print(strategy.format_signal('BTC', signal_hold))

    # Test position sizing
    print("\n4. Testing position sizing:")
    available_cash = 10000.00
    for symbol in ['BTC', 'SPY', 'QQQ', 'AMZN']:
        price = 42000 if symbol == 'BTC' else 150
        quantity = strategy.calculate_position_size(symbol, price, available_cash)
        value = quantity * price
        print(f"   {symbol}: {quantity} @ ${price:.2f} = ${value:.2f}")


if __name__ == "__main__":
    test_breakout_strategy()
