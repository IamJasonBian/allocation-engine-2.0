"""
Tests for _handle_order_replacement and lot-order enforcement in TradingSystem
"""
from unittest.mock import MagicMock, patch, call

from trading_system.config import DEFAULT_LOT_SIZE
from trading_system.main import TradingSystem


def _make_system():
    """Build a TradingSystem with mocked dependencies so no real I/O occurs."""
    with patch('trading_system.main.TwelveDataProvider'), \
         patch('trading_system.main.SafeCashBot') as MockBot:
        bot_instance = MockBot.return_value
        bot_instance.get_pdt_status.return_value = {
            'day_trade_count': 0,
            'flagged': False,
            'trades': [],
        }
        bot_instance.cancel_order_by_id.return_value = True

        system = TradingSystem(
            twelve_data_api_key='fake',
            symbols=['SPY'],
            dry_run=True,
            strategy_name='momentum_dca_long',
        )
    return system


def _make_signal(current_price=450.0):
    """Create a COVER_GAP signal dict that process_signal would receive."""
    stop = round(current_price * (1 - 0.01), 2)
    buy = round(stop - 0.20, 2)
    return {
        'signal': 'COVER_GAP',
        'reason': 'test',
        'order': {
            'action': 'stop_limit_sell',
            'symbol': 'SPY',
            'quantity': DEFAULT_LOT_SIZE,
            'stop_price': stop,
            'limit_price': stop,
            'current_price': current_price,
        },
        'paired_buy': {
            'action': 'limit_buy',
            'symbol': 'SPY',
            'quantity': DEFAULT_LOT_SIZE,
            'price': buy,
            'current_price': current_price,
        },
    }


def _buy_order(order_id='BUY-001', qty=DEFAULT_LOT_SIZE, created_at='2025-06-01 09:00:00'):
    return {
        'order_id': order_id,
        'symbol': 'SPY',
        'side': 'BUY',
        'order_type': 'Limit',
        'quantity': qty,
        'limit_price': 440.0,
        'stop_price': None,
        'created_at': created_at,
        'state': 'queued',
    }


def _sell_order(order_id='SELL-001', qty=DEFAULT_LOT_SIZE, created_at='2025-06-01 08:00:00'):
    return {
        'order_id': order_id,
        'symbol': 'SPY',
        'side': 'SELL',
        'order_type': 'Stop Limit',
        'quantity': qty,
        'limit_price': 445.0,
        'stop_price': 445.5,
        'created_at': created_at,
        'state': 'queued',
    }


class TestBothSellsCancelled:
    def test_both_sells_cancelled_and_replaced(self):
        """2 sell orders, no buy → cancels both sells and places fresh pair."""
        system = _make_system()
        signal = _make_signal()
        symbol_orders = [_sell_order('S1'), _sell_order('S2')]

        system._handle_order_replacement('SPY', signal, symbol_orders)

        # Both sells should be cancelled
        calls = system.trading_bot.cancel_order_by_id.call_args_list
        cancelled_ids = {c[0][0] for c in calls}
        assert cancelled_ids == {'S1', 'S2'}


class TestReplacesAllLotOrders:
    def test_replaces_all_lot_orders(self):
        """1 sell + 1 buy matching lot_size → cancels both, places fresh pair."""
        system = _make_system()
        signal = _make_signal(current_price=450.0)

        symbol_orders = [_sell_order('SELL-OLD'), _buy_order('BUY-OLD')]

        system._handle_order_replacement('SPY', signal, symbol_orders)

        calls = system.trading_bot.cancel_order_by_id.call_args_list
        cancelled_ids = {c[0][0] for c in calls}
        assert cancelled_ids == {'SELL-OLD', 'BUY-OLD'}


class TestQtyMismatchSkipsSide:
    def test_qty_mismatch_skips_that_side(self):
        """Buy qty != lot_size → only sell is cancelled, buy is left alone."""
        system = _make_system()
        signal = _make_signal()
        symbol_orders = [_sell_order(), _buy_order(qty=50)]  # lot_size=DEFAULT_LOT_SIZE, buy qty=50

        system._handle_order_replacement('SPY', signal, symbol_orders)

        # Only the sell (qty=200) should be cancelled, not the buy (qty=50)
        system.trading_bot.cancel_order_by_id.assert_called_once_with('SELL-001')


class TestPdtCount2AlertsAndSkips:
    @patch('trading_system.main.send_slack_alert')
    def test_pdt_count_2_alerts_and_skips(self, mock_slack):
        """PDT at 2/3 → Slack alert, no replacement."""
        system = _make_system()
        system.trading_bot.get_pdt_status.return_value = {
            'day_trade_count': 2,
            'flagged': False,
            'trades': [],
        }
        signal = _make_signal()
        symbol_orders = [_sell_order(), _buy_order()]

        system._handle_order_replacement('SPY', signal, symbol_orders)

        mock_slack.assert_called_once()
        assert 'PDT day trade count at 2/3' in mock_slack.call_args[0][0]
        system.trading_bot.cancel_order_by_id.assert_not_called()


class TestPdtFlaggedAlertsAndSkips:
    @patch('trading_system.main.send_slack_alert')
    def test_pdt_flagged_alerts_and_skips(self, mock_slack):
        """PDT flagged → Slack alert, no replacement."""
        system = _make_system()
        system.trading_bot.get_pdt_status.return_value = {
            'day_trade_count': 0,
            'flagged': True,
            'trades': [],
        }
        signal = _make_signal()
        symbol_orders = [_sell_order(), _buy_order()]

        system._handle_order_replacement('SPY', signal, symbol_orders)

        mock_slack.assert_called_once()
        assert 'PDT FLAGGED' in mock_slack.call_args[0][0]
        system.trading_bot.cancel_order_by_id.assert_not_called()


class TestPdtSafeProceeds:
    @patch('trading_system.main.send_slack_alert')
    def test_pdt_safe_proceeds(self, mock_slack):
        """PDT at 0 or 1 → replacement proceeds normally."""
        system = _make_system()
        system.trading_bot.get_pdt_status.return_value = {
            'day_trade_count': 1,
            'flagged': False,
            'trades': [],
        }
        signal = _make_signal()
        symbol_orders = [_sell_order(), _buy_order()]

        system._handle_order_replacement('SPY', signal, symbol_orders)

        mock_slack.assert_not_called()
        # Both sell and buy should be cancelled
        assert system.trading_bot.cancel_order_by_id.call_count == 2


class TestPdtNoneProceeds:
    def test_pdt_none_proceeds(self):
        """get_pdt_status returns None (API error) → proceed with replacement."""
        system = _make_system()
        system.trading_bot.get_pdt_status.return_value = None
        signal = _make_signal()
        symbol_orders = [_sell_order(), _buy_order()]

        system._handle_order_replacement('SPY', signal, symbol_orders)

        # Both sell and buy should be cancelled
        assert system.trading_bot.cancel_order_by_id.call_count == 2


class TestCancelFailsStillPlaces:
    def test_cancel_fails_still_places(self):
        """cancel_order_by_id returns False → placement still proceeds
        (order likely already filled/cancelled)."""
        system = _make_system()
        system.trading_bot.cancel_order_by_id.return_value = False
        signal = _make_signal()
        symbol_orders = [_sell_order(), _buy_order()]

        system._execute_stop_limit_sell_order = MagicMock()
        system._execute_paired_limit_buy = MagicMock()

        system._handle_order_replacement('SPY', signal, symbol_orders)

        # Placement still happens (cancel failure typically means order already gone)
        system._execute_stop_limit_sell_order.assert_called_once()
        system._execute_paired_limit_buy.assert_called_once()


class TestMomentumPricingUsed:
    def test_momentum_pricing_used(self):
        """Verify stop = price*(1-offset), buy = stop - buy_offset."""
        system = _make_system()
        current_price = 500.0
        signal = _make_signal(current_price=current_price)
        symbol_orders = [_sell_order(), _buy_order()]

        system._execute_stop_limit_sell_order = MagicMock()
        system._execute_paired_limit_buy = MagicMock()

        system._handle_order_replacement('SPY', signal, symbol_orders)

        # strategy defaults: stop_offset_pct=0.01, buy_offset=0.20
        expected_stop = round(current_price * (1 - 0.01), 2)  # 495.0
        expected_buy = round(expected_stop - 0.20, 2)  # 494.80

        sell_call = system._execute_stop_limit_sell_order.call_args
        assert sell_call[0][1]['stop_price'] == expected_stop
        assert sell_call[0][1]['limit_price'] == expected_stop
        assert sell_call[0][1]['quantity'] == DEFAULT_LOT_SIZE

        buy_call = system._execute_paired_limit_buy.call_args
        assert buy_call[0][1]['price'] == expected_buy
        assert buy_call[0][1]['quantity'] == DEFAULT_LOT_SIZE


class TestNormalFlowCancelsExistingSell:
    def test_stop_limit_sell_cancels_existing_sell(self):
        """process_signal with < 2 orders still cancels existing lot-sized sell
        before placing new one."""
        system = _make_system()
        signal = _make_signal()
        # Only 1 existing sell (< 2 orders, so normal flow is used)
        open_orders = [_sell_order('EXISTING-SELL')]

        system._execute_stop_limit_sell_order = MagicMock()
        system._execute_paired_limit_buy = MagicMock()

        system.process_signal('SPY', signal, open_orders)

        # Existing sell should be cancelled before new pair is placed
        system.trading_bot.cancel_order_by_id.assert_called_once_with('EXISTING-SELL')
        system._execute_stop_limit_sell_order.assert_called_once()
        system._execute_paired_limit_buy.assert_called_once()
