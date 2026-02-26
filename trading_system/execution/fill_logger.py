"""
Fill Logger — persistent fill quality logging.

Writes every order attempt and fill to fill_log.json for analysis.
"""

import json
import os
import tempfile
import uuid
from datetime import datetime


class FillLogger:
    """Logs order submissions, fills, and cancellations for quality analysis."""

    def __init__(self):
        self._log_path = os.path.join(
            os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
            'fill_log.json'
        )
        self._records = []
        self.load()

    def log_submission(self, symbol, side, intended_price, bid_at_submit=None,
                       ask_at_submit=None, order_id=None) -> str:
        """Record an order submission.

        Returns:
            submission_id for later matching to fill/cancel.
        """
        submission_id = uuid.uuid4().hex[:8]

        mid = None
        spread = None
        spread_bps = None
        if bid_at_submit is not None and ask_at_submit is not None:
            mid = (bid_at_submit + ask_at_submit) / 2.0
            spread = ask_at_submit - bid_at_submit
            spread_bps = round(spread / mid * 10000, 2) if mid > 0 else 0

        record = {
            'submission_id': submission_id,
            'timestamp': datetime.utcnow().isoformat(),
            'symbol': symbol,
            'side': side,
            'intended_price': intended_price,
            'bid_at_submit': bid_at_submit,
            'ask_at_submit': ask_at_submit,
            'mid_at_submit': round(mid, 6) if mid is not None else None,
            'spread_at_submit': round(spread, 6) if spread is not None else None,
            'spread_bps_at_submit': spread_bps,
            'order_id': order_id,
            'status': 'submitted',
            'fill_price': None,
            'fill_timestamp': None,
            'slippage_vs_mid': None,
            'slippage_vs_mid_bps': None,
            'cancel_reason': None,
        }

        self._records.append(record)
        self.save()
        return submission_id

    def log_fill(self, submission_id, fill_price, fill_timestamp=None):
        """Match a fill to a submission and compute slippage."""
        record = self._find_record(submission_id)
        if not record:
            return

        record['status'] = 'filled'
        record['fill_price'] = fill_price
        record['fill_timestamp'] = fill_timestamp or datetime.utcnow().isoformat()

        mid = record.get('mid_at_submit')
        if mid and mid > 0:
            if record['side'].lower() == 'buy':
                slippage = fill_price - mid
            else:
                slippage = mid - fill_price
            record['slippage_vs_mid'] = round(slippage, 6)
            record['slippage_vs_mid_bps'] = round(slippage / mid * 10000, 2)

        self.save()

    def log_cancel(self, submission_id, reason=""):
        """Mark a submission as cancelled."""
        record = self._find_record(submission_id)
        if not record:
            return

        record['status'] = 'cancelled'
        record['cancel_reason'] = reason
        self.save()

    def get_stats(self, symbol=None, last_n=None) -> dict:
        """Return aggregate fill quality stats.

        Args:
            symbol: Optional filter by symbol
            last_n: Optional limit to last N submissions
        """
        records = self._records
        if symbol:
            records = [r for r in records if r.get('symbol') == symbol]
        if last_n:
            records = records[-last_n:]

        if not records:
            return {}

        total_submissions = len(records)
        filled = [r for r in records if r.get('status') == 'filled']
        total_fills = len(filled)
        cancelled = len([r for r in records if r.get('status') == 'cancelled'])

        slippages_bps = [r['slippage_vs_mid_bps'] for r in filled
                         if r.get('slippage_vs_mid_bps') is not None]
        avg_slippage_bps = (sum(slippages_bps) / len(slippages_bps)
                            if slippages_bps else 0)

        fill_rate = total_fills / total_submissions if total_submissions > 0 else 0

        # Total cost impact (slippage * quantity, approximated)
        total_cost_impact = 0
        for r in filled:
            slip = r.get('slippage_vs_mid')
            if slip is not None:
                total_cost_impact += abs(slip)

        return {
            'total_submissions': total_submissions,
            'total_fills': total_fills,
            'total_cancelled': cancelled,
            'fill_rate': round(fill_rate, 4),
            'avg_slippage_bps': round(avg_slippage_bps, 2),
            'total_cost_impact': round(total_cost_impact, 4),
        }

    def save(self):
        """Persist records to fill_log.json atomically."""
        try:
            dir_name = os.path.dirname(self._log_path)
            if dir_name and not os.path.exists(dir_name):
                os.makedirs(dir_name, exist_ok=True)
            fd, tmp_path = tempfile.mkstemp(dir=dir_name or '.', suffix='.tmp')
            try:
                with os.fdopen(fd, 'w') as f:
                    json.dump(self._records, f, indent=2)
                os.replace(tmp_path, self._log_path)
            except Exception:
                if os.path.exists(tmp_path):
                    os.unlink(tmp_path)
                raise
        except Exception as e:
            print(f"  [fill_logger] save error: {e}")

    def load(self):
        """Load records from fill_log.json."""
        try:
            if os.path.exists(self._log_path):
                with open(self._log_path, 'r') as f:
                    self._records = json.load(f)
            else:
                self._records = []
        except Exception as e:
            print(f"  [fill_logger] load error: {e}")
            self._records = []

    def _find_record(self, submission_id):
        """Find a record by submission_id."""
        for r in reversed(self._records):
            if r.get('submission_id') == submission_id:
                return r
        return None
