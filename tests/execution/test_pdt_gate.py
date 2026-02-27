"""Tests for PDTGate"""

from trading_system.execution.pdt_gate import PDTGate


class MockTradingBot:
    """Mock SafeCashBot for PDT testing."""

    def __init__(self, pdt_status=None, open_orders=None):
        self._pdt_status = pdt_status
        self._open_orders = open_orders or []

    def get_pdt_status(self):
        return self._pdt_status

    def get_open_orders(self):
        return self._open_orders


class TestPDTGate:
    def test_flagged_blocks_buys(self):
        bot = MockTradingBot(pdt_status={'flagged': True, 'day_trade_count': 3})
        gate = PDTGate(trading_bot=bot)
        allowed, reason = gate.can_place_order('SPY', 'buy')
        assert allowed is False
        assert 'FLAGGED' in reason

    def test_flagged_allows_sells(self):
        bot = MockTradingBot(pdt_status={'flagged': True, 'day_trade_count': 3})
        gate = PDTGate(trading_bot=bot)
        allowed, reason = gate.can_place_order('SPY', 'sell')
        assert allowed is True
        assert 'position-closing' in reason

    def test_high_day_trade_count_blocks_round_trip(self):
        bot = MockTradingBot(
            pdt_status={'flagged': False, 'day_trade_count': 2},
            open_orders=[{
                'symbol': 'SPY',
                'side': 'SELL',
                'created_at': __import__('datetime').date.today().isoformat() + ' 10:00:00',
            }]
        )
        gate = PDTGate(trading_bot=bot)
        allowed, reason = gate.can_place_order('SPY', 'buy')
        assert allowed is False
        assert 'PDT risk' in reason

    def test_zero_day_trades_allows_all(self):
        bot = MockTradingBot(pdt_status={'flagged': False, 'day_trade_count': 0})
        gate = PDTGate(trading_bot=bot)
        allowed, reason = gate.can_place_order('SPY', 'buy')
        assert allowed is True

    def test_api_failure_blocks_order(self):
        """PDT gate fails CLOSED when status unavailable."""
        bot = MockTradingBot(pdt_status=None)
        gate = PDTGate(trading_bot=bot)
        allowed, reason = gate.can_place_order('SPY', 'buy')
        assert allowed is False
        assert 'unavailable' in reason

    def test_no_trading_bot_allows_order(self):
        gate = PDTGate(trading_bot=None)
        allowed, reason = gate.can_place_order('SPY', 'buy')
        assert allowed is True

    def test_warning_at_one_day_trade(self):
        bot = MockTradingBot(
            pdt_status={'flagged': False, 'day_trade_count': 1},
            open_orders=[{
                'symbol': 'SPY',
                'side': 'SELL',
                'created_at': __import__('datetime').date.today().isoformat() + ' 10:00:00',
            }]
        )
        gate = PDTGate(trading_bot=bot)
        allowed, reason = gate.can_place_order('SPY', 'buy')
        assert allowed is True
        assert 'warning' in reason.lower()
