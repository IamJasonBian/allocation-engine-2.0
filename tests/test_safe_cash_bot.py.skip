"""
Tests for SafeCashBot.get_open_orders() method
"""
import pytest
from unittest.mock import Mock, patch
import sys
import os

# Add parent directory to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from utils.safe_cash_bot import SafeCashBot


@pytest.fixture
def mock_bot():
    """Create a mock SafeCashBot instance"""
    with patch('utils.safe_cash_bot.r') as mock_r:
        mock_r.login.return_value = {'access_token': 'test_token'}
        bot = SafeCashBot()
        bot.auth = Mock()
        yield bot


class TestGetOpenOrders:
    """Test suite for SafeCashBot.get_open_orders()"""

    def test_instrument_id_resolution_for_btc(self, mock_bot):
        """Test critical BTC fix: orders without symbol resolve from instrument_id"""
        mock_orders = [
            {
                'id': 'order-btc',
                'instrument_id': 'btc-instrument-123',
                'side': 'sell',
                'type': 'limit',
                'trigger': 'stop',
                'state': 'confirmed',
                'quantity': '500',
                'price': '30.50',
                'stop_price': '30.50',
                'created_at': '2026-02-06T10:00:00Z',
                'updated_at': '2026-02-06T10:00:00Z'
            }
        ]

        with patch('utils.safe_cash_bot.r.orders.get_all_open_stock_orders') as mock_get_orders, \
             patch('utils.safe_cash_bot.r.stocks.get_instrument_by_url') as mock_get_instrument:

            mock_get_orders.return_value = mock_orders
            mock_get_instrument.return_value = {'symbol': 'BTC'}

            orders = mock_bot.get_open_orders()

            assert len(orders) == 1
            assert orders[0]['symbol'] == 'BTC'
            assert orders[0]['order_type'] == 'Stop Limit'
            mock_get_instrument.assert_called_once()

    def test_order_type_detection(self, mock_bot):
        """Test all order types are correctly detected"""
        mock_orders = [
            {'id': '1', 'symbol': 'A', 'side': 'sell', 'type': 'limit', 'trigger': 'immediate',
             'state': 'confirmed', 'quantity': '10', 'price': '100', 'stop_price': None,
             'created_at': '2026-02-06T10:00:00Z', 'updated_at': '2026-02-06T10:00:00Z'},
            {'id': '2', 'symbol': 'B', 'side': 'sell', 'type': 'limit', 'trigger': 'stop',
             'state': 'confirmed', 'quantity': '20', 'price': '200', 'stop_price': '195',
             'created_at': '2026-02-06T10:00:00Z', 'updated_at': '2026-02-06T10:00:00Z'},
            {'id': '3', 'symbol': 'C', 'side': 'sell', 'type': 'market', 'trigger': 'stop',
             'state': 'confirmed', 'quantity': '30', 'price': None, 'stop_price': '300',
             'created_at': '2026-02-06T10:00:00Z', 'updated_at': '2026-02-06T10:00:00Z'},
        ]

        with patch('utils.safe_cash_bot.r.orders.get_all_open_stock_orders') as mock_get_orders:
            mock_get_orders.return_value = mock_orders
            orders = mock_bot.get_open_orders()

            assert orders[0]['order_type'] == 'Limit'
            assert orders[1]['order_type'] == 'Stop Limit'
            assert orders[2]['order_type'] == 'Stop Loss'

    def test_error_handling(self, mock_bot):
        """Test graceful error handling for API failures and empty responses"""
        # Test API exception
        with patch('utils.safe_cash_bot.r.orders.get_all_open_stock_orders') as mock_get_orders:
            mock_get_orders.side_effect = Exception("Network error")
            assert mock_bot.get_open_orders() == []

        # Test empty/None responses
        with patch('utils.safe_cash_bot.r.orders.get_all_open_stock_orders') as mock_get_orders:
            mock_get_orders.return_value = []
            assert mock_bot.get_open_orders() == []

            mock_get_orders.return_value = None
            assert mock_bot.get_open_orders() == []

        # Test instrument lookup failure
        with patch('utils.safe_cash_bot.r.orders.get_all_open_stock_orders') as mock_get_orders, \
             patch('utils.safe_cash_bot.r.stocks.get_instrument_by_url') as mock_get_instrument:

            mock_get_orders.return_value = [
                {'id': 'x', 'instrument_id': 'bad-id', 'side': 'sell', 'type': 'limit',
                 'trigger': 'immediate', 'state': 'confirmed', 'quantity': '10', 'price': '50',
                 'stop_price': None, 'created_at': '2026-02-06T10:00:00Z', 'updated_at': '2026-02-06T10:00:00Z'}
            ]
            mock_get_instrument.side_effect = Exception("API Error")

            orders = mock_bot.get_open_orders()
            assert orders[0]['symbol'] == 'N/A'
