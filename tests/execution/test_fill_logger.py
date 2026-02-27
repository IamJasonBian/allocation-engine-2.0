"""Tests for FillLogger"""

import json
import os
import tempfile

from trading_system.execution.fill_logger import FillLogger


def _make_logger(tmp_dir):
    """Create a FillLogger pointing at a temp directory."""
    logger = FillLogger()
    logger._log_path = os.path.join(tmp_dir, 'fill_log.json')
    logger._records = []
    return logger


class TestLogSubmission:
    def test_creates_record(self):
        with tempfile.TemporaryDirectory() as tmp:
            logger = _make_logger(tmp)
            sid = logger.log_submission('SPY', 'buy', 450.0, 449.90, 450.10)
            assert sid is not None
            assert len(sid) == 8
            assert len(logger._records) == 1
            assert logger._records[0]['symbol'] == 'SPY'
            assert logger._records[0]['side'] == 'buy'
            assert logger._records[0]['status'] == 'submitted'

    def test_computes_mid_and_spread(self):
        with tempfile.TemporaryDirectory() as tmp:
            logger = _make_logger(tmp)
            logger.log_submission('SPY', 'buy', 450.0, 449.90, 450.10)
            record = logger._records[0]
            assert record['mid_at_submit'] is not None
            assert abs(record['mid_at_submit'] - 450.0) < 0.01
            assert record['spread_at_submit'] is not None
            assert abs(record['spread_at_submit'] - 0.20) < 0.01

    def test_handles_none_bid_ask(self):
        with tempfile.TemporaryDirectory() as tmp:
            logger = _make_logger(tmp)
            sid = logger.log_submission('SPY', 'buy', 450.0)
            assert sid is not None
            assert logger._records[0]['mid_at_submit'] is None


class TestLogFill:
    def test_matches_submission_and_computes_slippage(self):
        with tempfile.TemporaryDirectory() as tmp:
            logger = _make_logger(tmp)
            sid = logger.log_submission('SPY', 'buy', 450.0, 449.90, 450.10)
            logger.log_fill(sid, 450.05)
            record = logger._records[0]
            assert record['status'] == 'filled'
            assert record['fill_price'] == 450.05
            # Slippage for buy: fill_price - mid = 450.05 - 450.0 = +0.05
            assert record['slippage_vs_mid'] is not None
            assert record['slippage_vs_mid'] > 0

    def test_missing_submission_id_no_error(self):
        with tempfile.TemporaryDirectory() as tmp:
            logger = _make_logger(tmp)
            logger.log_fill('nonexistent', 450.0)  # Should not raise


class TestLogCancel:
    def test_marks_cancelled(self):
        with tempfile.TemporaryDirectory() as tmp:
            logger = _make_logger(tmp)
            sid = logger.log_submission('SPY', 'buy', 450.0)
            logger.log_cancel(sid, reason='wide spread')
            assert logger._records[0]['status'] == 'cancelled'
            assert logger._records[0]['cancel_reason'] == 'wide spread'


class TestSaveLoad:
    def test_roundtrip(self):
        with tempfile.TemporaryDirectory() as tmp:
            logger = _make_logger(tmp)
            sid1 = logger.log_submission('SPY', 'buy', 450.0, 449.90, 450.10)
            sid2 = logger.log_submission('BTC', 'sell', 31.0, 30.95, 31.05)
            logger.log_fill(sid1, 450.05)

            # Load into a new logger
            logger2 = _make_logger(tmp)
            logger2._log_path = logger._log_path
            logger2.load()
            assert len(logger2._records) == 2
            assert logger2._records[0]['status'] == 'filled'
            assert logger2._records[1]['status'] == 'submitted'


class TestGetStats:
    def test_basic_stats(self):
        with tempfile.TemporaryDirectory() as tmp:
            logger = _make_logger(tmp)
            sid1 = logger.log_submission('SPY', 'buy', 450.0, 449.90, 450.10)
            sid2 = logger.log_submission('SPY', 'buy', 451.0, 450.90, 451.10)
            logger.log_fill(sid1, 450.05)
            logger.log_fill(sid2, 451.0)

            stats = logger.get_stats()
            assert stats['total_submissions'] == 2
            assert stats['total_fills'] == 2
            assert stats['fill_rate'] == 1.0

    def test_stats_with_symbol_filter(self):
        with tempfile.TemporaryDirectory() as tmp:
            logger = _make_logger(tmp)
            logger.log_submission('SPY', 'buy', 450.0)
            logger.log_submission('BTC', 'sell', 31.0)

            stats = logger.get_stats(symbol='SPY')
            assert stats['total_submissions'] == 1

    def test_empty_stats(self):
        with tempfile.TemporaryDirectory() as tmp:
            logger = _make_logger(tmp)
            stats = logger.get_stats()
            assert stats == {}
