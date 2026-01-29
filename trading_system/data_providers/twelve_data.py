"""
Twelve Data API Integration
Provides market data for stocks and crypto using Twelve Data API
"""

import requests
from typing import Dict, List, Optional


class TwelveDataProvider:
    """Market data provider using Twelve Data API"""

    BASE_URL = "https://api.twelvedata.com"

    # Symbol mappings for Twelve Data API
    SYMBOL_MAP = {
        'BTC': 'BTC/USD',
        'SPY': 'SPY',  # S&P 500 ETF
        'QQQ': 'QQQ',
        'AMZN': 'AMZN'
    }

    def __init__(self, api_key: str):
        """
        Initialize Twelve Data provider

        Args:
            api_key: Twelve Data API key
        """
        self.api_key = api_key
        self.session = requests.Session()

    def _make_request(self, endpoint: str, params: Dict) -> Optional[Dict]:
        """
        Make API request to Twelve Data

        Args:
            endpoint: API endpoint
            params: Query parameters

        Returns:
            API response as dict or None on error
        """
        params['apikey'] = self.api_key
        url = f"{self.BASE_URL}/{endpoint}"

        try:
            response = self.session.get(url, params=params, timeout=10)
            response.raise_for_status()
            data = response.json()

            # Check for API errors
            if 'status' in data and data['status'] == 'error':
                print(f"API Error: {data.get('message', 'Unknown error')}")
                return None

            return data

        except requests.exceptions.RequestException as e:
            print(f"Request failed: {e}")
            return None

    def get_intraday_data(self, symbol: str, interval: str = '5min',
                          outputsize: int = 390) -> Optional[List[Dict]]:
        """
        Get intraday price data

        Args:
            symbol: Stock symbol (BTC, SPY, QQQ, AMZN)
            interval: Time interval (1min, 5min, 15min, 30min, 1h)
            outputsize: Number of data points to return

        Returns:
            List of price data points with OHLCV data
        """
        # Map symbol to Twelve Data format
        td_symbol = self.SYMBOL_MAP.get(symbol, symbol)

        params = {
            'symbol': td_symbol,
            'interval': interval,
            'outputsize': outputsize,
            'format': 'JSON'
        }

        data = self._make_request('time_series', params)

        if not data or 'values' not in data:
            return None

        # Convert to standard format
        result = []
        for point in data['values']:
            result.append({
                'datetime': point['datetime'],
                'open': float(point['open']),
                'high': float(point['high']),
                'low': float(point['low']),
                'close': float(point['close']),
                'volume': int(point.get('volume', 0))
            })

        return result

    def get_daily_data(self, symbol: str, outputsize: int = 30) -> Optional[List[Dict]]:
        """
        Get daily price data (for 30-day high/low calculation)

        Args:
            symbol: Stock symbol
            outputsize: Number of days to return (default 30)

        Returns:
            List of daily price data
        """
        td_symbol = self.SYMBOL_MAP.get(symbol, symbol)

        params = {
            'symbol': td_symbol,
            'interval': '1day',
            'outputsize': outputsize,
            'format': 'JSON'
        }

        data = self._make_request('time_series', params)

        if not data or 'values' not in data:
            return None

        # Convert to standard format
        result = []
        for point in data['values']:
            result.append({
                'date': point['datetime'],
                'open': float(point['open']),
                'high': float(point['high']),
                'low': float(point['low']),
                'close': float(point['close']),
                'volume': int(point.get('volume', 0))
            })

        return result

    def get_quote(self, symbol: str) -> Optional[Dict]:
        """
        Get real-time quote

        Args:
            symbol: Stock symbol

        Returns:
            Current price and quote data
        """
        td_symbol = self.SYMBOL_MAP.get(symbol, symbol)

        params = {
            'symbol': td_symbol,
            'format': 'JSON'
        }

        data = self._make_request('quote', params)

        if not data:
            return None

        return {
            'symbol': symbol,
            'price': float(data.get('close', 0)),
            'open': float(data.get('open', 0)),
            'high': float(data.get('high', 0)),
            'low': float(data.get('low', 0)),
            'volume': int(data.get('volume', 0)),
            'timestamp': data.get('timestamp', '')
        }

    def get_multi_quote(self, symbols: List[str]) -> Dict[str, Dict]:
        """
        Get quotes for multiple symbols in one request

        Args:
            symbols: List of stock symbols

        Returns:
            Dictionary mapping symbols to quote data
        """
        # Map symbols
        td_symbols = [self.SYMBOL_MAP.get(s, s) for s in symbols]
        symbol_str = ','.join(td_symbols)

        params = {
            'symbol': symbol_str,
            'format': 'JSON'
        }

        data = self._make_request('quote', params)

        if not data:
            return {}

        # Handle single vs multiple symbols response format
        quotes = data if isinstance(data, list) else [data]

        result = {}
        for i, quote_data in enumerate(quotes):
            original_symbol = symbols[i]
            result[original_symbol] = {
                'symbol': original_symbol,
                'price': float(quote_data.get('close', 0)),
                'open': float(quote_data.get('open', 0)),
                'high': float(quote_data.get('high', 0)),
                'low': float(quote_data.get('low', 0)),
                'volume': int(quote_data.get('volume', 0)),
                'timestamp': quote_data.get('timestamp', '')
            }

        return result


def test_twelve_data():
    """Test function for Twelve Data integration"""
    import os
    from dotenv import load_dotenv

    load_dotenv()
    api_key = os.getenv('TWELVE_DATA_API_KEY', 'f2c57fbb0a794024b0defff74af45686')

    provider = TwelveDataProvider(api_key)

    print("Testing Twelve Data API Integration")
    print("=" * 70)

    # Test single quote
    print("\n1. Testing single quote (BTC):")
    quote = provider.get_quote('BTC')
    if quote:
        print(f"   BTC Price: ${quote['price']:,.2f}")
        print(f"   High: ${quote['high']:,.2f}, Low: ${quote['low']:,.2f}")

    # Test multiple quotes
    print("\n2. Testing multiple quotes:")
    symbols = ['BTC', 'SPY', 'QQQ', 'AMZN']
    quotes = provider.get_multi_quote(symbols)
    for symbol, data in quotes.items():
        print(f"   {symbol}: ${data['price']:,.2f}")

    # Test daily data
    print("\n3. Testing 30-day daily data (SPY):")
    daily = provider.get_daily_data('SPY', outputsize=30)
    if daily:
        print(f"   Retrieved {len(daily)} days of data")
        print(f"   Latest: {daily[0]['date']} - Close: ${daily[0]['close']:.2f}")

    # Test intraday data
    print("\n4. Testing intraday data (AMZN):")
    intraday = provider.get_intraday_data('AMZN', interval='5min', outputsize=10)
    if intraday:
        print(f"   Retrieved {len(intraday)} data points")
        print(f"   Latest: {intraday[0]['datetime']} - Close: ${intraday[0]['close']:.2f}")

    print("\n" + "=" * 70)


if __name__ == "__main__":
    test_twelve_data()
