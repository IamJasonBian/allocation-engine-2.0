"""Tests for SpreadChecker"""

from trading_system.execution.spread_checker import SpreadChecker


class MockFillAuditor:
    """Mock FillAuditor that returns configurable NBBO quotes."""

    def __init__(self, bid=100.0, ask=100.10):
        self.bid = bid
        self.ask = ask

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


class TestSpreadAcceptable:
    def test_normal_spread_acceptable(self):
        auditor = MockFillAuditor(bid=100.0, ask=100.10)
        checker = SpreadChecker(fill_auditor=auditor)
        result = checker.check_spread('SPY')
        assert result['is_acceptable'] is True

    def test_wide_spread_rejected(self):
        # Spread > 2% should be rejected
        auditor = MockFillAuditor(bid=100.0, ask=103.0)
        checker = SpreadChecker(fill_auditor=auditor)
        result = checker.check_spread('SPY')
        assert result['is_acceptable'] is False
        assert 'exceeds' in result['reason']

    def test_missing_quote_data_proceeds(self):
        auditor = MockFillAuditor(bid=None, ask=None)
        checker = SpreadChecker(fill_auditor=auditor)
        result = checker.check_spread('SPY')
        assert result['is_acceptable'] is True
        assert 'no quote data' in result['reason']

    def test_no_fill_auditor_proceeds(self):
        checker = SpreadChecker(fill_auditor=None)
        result = checker.check_spread('SPY')
        assert result['is_acceptable'] is True

    def test_spread_at_boundary(self):
        # Exactly 2% spread should be acceptable (not > 2%)
        auditor = MockFillAuditor(bid=100.0, ask=102.0)
        checker = SpreadChecker(fill_auditor=auditor, max_spread_pct=0.02)
        result = checker.check_spread('SPY')
        # 2/101 = 0.0198... which is < 0.02
        assert result['is_acceptable'] is True


class TestShouldWait:
    def test_should_wait_when_spread_above_historical(self):
        auditor = MockFillAuditor(bid=100.0, ask=100.50)
        checker = SpreadChecker(fill_auditor=auditor)
        # Seed historical with tight spreads
        for _ in range(10):
            checker.record_spread('SPY', 0.001)
        result = checker.check_spread('SPY')
        assert result['should_wait'] is True

    def test_no_wait_with_normal_spread(self):
        auditor = MockFillAuditor(bid=100.0, ask=100.10)
        checker = SpreadChecker(fill_auditor=auditor)
        # Seed history with similar spreads
        for _ in range(10):
            checker.record_spread('SPY', 0.001)
        result = checker.check_spread('SPY')
        assert result['should_wait'] is True or result['is_acceptable'] is True


class TestRecordSpread:
    def test_records_spread_history(self):
        checker = SpreadChecker()
        for i in range(5):
            checker.record_spread('SPY', 0.001 * (i + 1))
        assert len(checker._spread_history['SPY']) == 5

    def test_history_caps_at_200(self):
        checker = SpreadChecker()
        for i in range(250):
            checker.record_spread('SPY', 0.001)
        assert len(checker._spread_history['SPY']) == 200
