"""
Metrics Calculation Utilities
Calculates trading metrics like volatility, highs, lows, etc.
"""

from typing import List, Dict
import statistics
import math


class MetricsCalculator:
    """Calculates various trading metrics from price data"""

    @staticmethod
    def calculate_intraday_volatility(intraday_data: List[Dict]) -> float:
        """
        Calculate intraday volatility as standard deviation of returns

        Args:
            intraday_data: List of intraday price data points

        Returns:
            Volatility as percentage
        """
        if not intraday_data or len(intraday_data) < 2:
            return 0.0

        # Calculate returns between consecutive data points
        returns = []
        for i in range(1, len(intraday_data)):
            prev_price = intraday_data[i-1]['close']
            curr_price = intraday_data[i]['close']

            if prev_price > 0:
                ret = (curr_price - prev_price) / prev_price
                returns.append(ret)

        if not returns:
            return 0.0

        # Calculate standard deviation of returns
        volatility = statistics.stdev(returns) if len(returns) > 1 else 0.0

        # Annualize intraday volatility (assuming 390 5-min periods in a trading day)
        # sqrt(periods_per_day) for intraday to daily conversion
        annualized_volatility = volatility * math.sqrt(390)

        return annualized_volatility * 100  # Convert to percentage

    @staticmethod
    def calculate_intraday_range(intraday_data: List[Dict]) -> Dict[str, float]:
        """
        Calculate intraday high and low

        Args:
            intraday_data: List of intraday price data points

        Returns:
            Dictionary with 'high' and 'low' keys
        """
        if not intraday_data:
            return {'high': 0.0, 'low': 0.0}

        highs = [d['high'] for d in intraday_data]
        lows = [d['low'] for d in intraday_data]

        return {
            'high': max(highs),
            'low': min(lows)
        }

    @staticmethod
    def calculate_30day_range(daily_data: List[Dict]) -> Dict[str, float]:
        """
        Calculate 30-day high and low

        Args:
            daily_data: List of daily price data (should have 30 days)

        Returns:
            Dictionary with '30d_high' and '30d_low' keys
        """
        if not daily_data:
            return {'30d_high': 0.0, '30d_low': 0.0}

        # Take up to 30 most recent days
        recent_data = daily_data[:30]

        highs = [d['high'] for d in recent_data]
        lows = [d['low'] for d in recent_data]

        return {
            '30d_high': max(highs),
            '30d_low': min(lows)
        }

    @staticmethod
    def calculate_all_metrics(intraday_data: List[Dict],
                              daily_data: List[Dict]) -> Dict:
        """
        Calculate all metrics for a symbol

        Args:
            intraday_data: Intraday price data
            daily_data: Daily price data (30 days)

        Returns:
            Dictionary with all calculated metrics
        """
        metrics = {}

        # Intraday metrics
        if intraday_data:
            metrics['intraday_volatility'] = MetricsCalculator.calculate_intraday_volatility(
                intraday_data
            )
            intraday_range = MetricsCalculator.calculate_intraday_range(intraday_data)
            metrics['intraday_high'] = intraday_range['high']
            metrics['intraday_low'] = intraday_range['low']
            metrics['current_price'] = intraday_data[0]['close']  # Most recent

        # 30-day metrics
        if daily_data:
            range_30d = MetricsCalculator.calculate_30day_range(daily_data)
            metrics['30d_high'] = range_30d['30d_high']
            metrics['30d_low'] = range_30d['30d_low']

        return metrics

    @staticmethod
    def format_metrics(symbol: str, metrics: Dict) -> str:
        """
        Format metrics for display

        Args:
            symbol: Stock symbol
            metrics: Metrics dictionary

        Returns:
            Formatted string
        """
        lines = [
            f"\n{'='*70}",
            f"METRICS: {symbol}",
            f"{'='*70}",
            f"Current Price:        ${metrics.get('current_price', 0):,.2f}",
            "",
            "Intraday Range:",
            f"  High:               ${metrics.get('intraday_high', 0):,.2f}",
            f"  Low:                ${metrics.get('intraday_low', 0):,.2f}",
            f"  Volatility:         {metrics.get('intraday_volatility', 0):.2f}%",
            "",
            "30-Day Range:",
            f"  30-Day High:        ${metrics.get('30d_high', 0):,.2f}",
            f"  30-Day Low:         ${metrics.get('30d_low', 0):,.2f}",
            f"{'='*70}\n"
        ]

        return '\n'.join(lines)


def test_metrics():
    """Test metrics calculation"""

    # Sample intraday data
    intraday = [
        {'datetime': '2024-01-24 15:55:00', 'open': 100, 'high': 102, 'low': 99, 'close': 101, 'volume': 1000},
        {'datetime': '2024-01-24 15:50:00', 'open': 99, 'high': 101, 'low': 98, 'close': 100, 'volume': 1000},
        {'datetime': '2024-01-24 15:45:00', 'open': 98, 'high': 100, 'low': 97, 'close': 99, 'volume': 1000},
        {'datetime': '2024-01-24 15:40:00', 'open': 97, 'high': 99, 'low': 96, 'close': 98, 'volume': 1000},
    ]

    # Sample daily data
    daily = [
        {'date': '2024-01-24', 'open': 100, 'high': 105, 'low': 95, 'close': 101, 'volume': 10000},
        {'date': '2024-01-23', 'open': 98, 'high': 103, 'low': 93, 'close': 100, 'volume': 10000},
        {'date': '2024-01-22', 'open': 95, 'high': 110, 'low': 90, 'close': 98, 'volume': 10000},
    ]

    calc = MetricsCalculator()

    print("Testing Metrics Calculator")
    print("=" * 70)

    # Test intraday volatility
    volatility = calc.calculate_intraday_volatility(intraday)
    print(f"\nIntraday Volatility: {volatility:.2f}%")

    # Test intraday range
    intraday_range = calc.calculate_intraday_range(intraday)
    print(f"Intraday High: ${intraday_range['high']:.2f}")
    print(f"Intraday Low: ${intraday_range['low']:.2f}")

    # Test 30-day range
    range_30d = calc.calculate_30day_range(daily)
    print(f"\n30-Day High: ${range_30d['30d_high']:.2f}")
    print(f"30-Day Low: ${range_30d['30d_low']:.2f}")

    # Test all metrics
    all_metrics = calc.calculate_all_metrics(intraday, daily)
    print(calc.format_metrics('TEST', all_metrics))


if __name__ == "__main__":
    test_metrics()
