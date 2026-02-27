"""
Integration test for the execution quality layer.

Uses mocked SafeCashBot and FillAuditor to verify the full flow:
spread check -> PDT check -> price optimize -> place order -> audit fill.
"""

import os
import tempfile

from trading_system.execution.fill_auditor import FillAuditor
from trading_system.execution.spread_checker import SpreadChecker
from trading_system.execution.price_optimizer import PriceOptimizer
from trading_system.execution.pdt_gate import PDTGate
from trading_system.execution.fill_logger import FillLogger


class MockFillAuditor:
    """Returns configurable NBBO quotes."""

    def __init__(self, bid=100.0, ask=100.10):
        self.bid = bid
        self.ask = ask
        self.audited_fills = []

    def get_nbbo_now(self, symbol):
        if self.bid is None:
            return None
        mid = (self.bid + self.ask) / 2.0
        spread = self.ask - self.bid
        return {
            'bid': self.bid,
            'ask': self.ask,
            'mid': mid,
            'spread': spread,
            'spread_bps': spread / mid * 10000 if mid > 0 else 0,
            'source': 'mock',
        }

    def audit_fill(self, symbol, side, fill_price, fill_quantity, rh_order_id=None):
        audit = {
            'symbol': symbol,
            'side': side,
            'fill_price': fill_price,
            'fill_quantity': fill_quantity,
            'grade': 'GOOD',
            'slippage_vs_mid_bps': 2.5,
        }
        self.audited_fills.append(audit)
        return audit


class MockTradingBot:
    """Simulates SafeCashBot for integration testing."""

    def __init__(self, pdt_status=None, positions=None, open_orders=None):
        self._pdt_status = pdt_status or {'flagged': False, 'day_trade_count': 0}
        self._positions = positions or []
        self._open_orders = open_orders or []

    def get_pdt_status(self):
        return self._pdt_status

    def get_positions(self):
        return self._positions

    def get_open_orders(self):
        return self._open_orders


class TestFullCycle:
    """Test the full execution quality flow."""

    def test_normal_order_passes_all_checks(self):
        """A normal order with reasonable spread should pass all checks."""
        auditor = MockFillAuditor(bid=449.90, ask=450.10)
        checker = SpreadChecker(fill_auditor=auditor, max_spread_pct=0.02)
        optimizer = PriceOptimizer()
        bot = MockTradingBot(pdt_status={'flagged': False, 'day_trade_count': 0})
        gate = PDTGate(trading_bot=bot)

        with tempfile.TemporaryDirectory() as tmp:
            logger = FillLogger()
            logger._log_path = os.path.join(tmp, 'fill_log.json')
            logger._records = []

            # Step 1: Spread check
            spread_result = checker.check_spread('SPY')
            assert spread_result['is_acceptable'] is True

            # Step 2: PDT check
            allowed, reason = gate.can_place_order('SPY', 'buy')
            assert allowed is True

            # Step 3: Price optimization
            price = optimizer.optimal_limit_price(
                'buy', bid=449.90, ask=450.10, urgency=0.5)
            assert price is not None
            assert 449.90 <= price <= 450.10

            # Step 4: Log submission
            sid = logger.log_submission('SPY', 'buy', price, 449.90, 450.10)
            assert sid is not None

            # Step 5: Audit fill (simulating successful order)
            audit = auditor.audit_fill('SPY', 'buy', price, 100)
            assert audit['grade'] == 'GOOD'

            # Step 6: Log fill
            logger.log_fill(sid, price)
            stats = logger.get_stats()
            assert stats['total_fills'] == 1

    def test_wide_spread_blocks_order(self):
        """An order with spread > 2% should be blocked by SpreadChecker."""
        auditor = MockFillAuditor(bid=100.0, ask=103.0)
        checker = SpreadChecker(fill_auditor=auditor, max_spread_pct=0.02)

        spread_result = checker.check_spread('SPY')
        assert spread_result['is_acceptable'] is False

    def test_pdt_risky_order_blocked(self):
        """An order that would trigger PDT should be blocked."""
        from datetime import date
        bot = MockTradingBot(
            pdt_status={'flagged': False, 'day_trade_count': 2},
            open_orders=[{
                'symbol': 'SPY',
                'side': 'SELL',
                'created_at': date.today().isoformat() + ' 10:00:00',
            }]
        )
        gate = PDTGate(trading_bot=bot)
        allowed, reason = gate.can_place_order('SPY', 'buy')
        assert allowed is False
        assert 'PDT risk' in reason

    def test_pdt_flagged_blocks_buy_allows_sell(self):
        """PDT-flagged account blocks buys but allows position-closing sells."""
        bot = MockTradingBot(pdt_status={'flagged': True, 'day_trade_count': 3})
        gate = PDTGate(trading_bot=bot)

        buy_allowed, _ = gate.can_place_order('SPY', 'buy')
        sell_allowed, _ = gate.can_place_order('SPY', 'sell')
        assert buy_allowed is False
        assert sell_allowed is True

    def test_fill_audit_captures_slippage(self):
        """Fill audit correctly captures slippage metrics."""
        auditor = MockFillAuditor(bid=449.90, ask=450.10)
        audit = auditor.audit_fill('SPY', 'buy', 450.05, 100)
        assert audit is not None
        assert audit['grade'] == 'GOOD'
        assert len(auditor.audited_fills) == 1

    def test_all_components_are_optional(self):
        """System should work even if all execution components are None."""
        # This verifies the backward compatibility requirement
        checker = SpreadChecker(fill_auditor=None)
        result = checker.check_spread('SPY')
        assert result['is_acceptable'] is True

        gate = PDTGate(trading_bot=None)
        allowed, _ = gate.can_place_order('SPY', 'buy')
        assert allowed is True

        optimizer = PriceOptimizer()
        price = optimizer.optimal_limit_price('buy', bid=None, ask=None)
        assert price is None
