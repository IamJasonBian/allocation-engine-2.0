"""
Spread Checker — pre-flight check before any order.

Answers "is the spread reasonable right now?" by comparing current spread
against configurable thresholds and historical medians.
"""

from collections import defaultdict, deque


class SpreadChecker:
    """Pre-flight spread check before order placement."""

    def __init__(self, fill_auditor=None, max_spread_pct: float = 0.02,
                 spread_history: dict = None):
        self._fill_auditor = fill_auditor
        self.max_spread_pct = max_spread_pct
        # Rolling history of spreads per symbol (last 200 observations)
        self._spread_history = defaultdict(lambda: deque(maxlen=200))
        if spread_history:
            for sym, values in spread_history.items():
                self._spread_history[sym].extend(values)

    def check_spread(self, symbol: str) -> dict:
        """Check if current spread is acceptable for trading.

        Returns:
            Dict with bid, ask, mid, spread_pct, is_acceptable, reason, should_wait.
        """
        if not self._fill_auditor:
            return {
                'bid': None, 'ask': None, 'mid': None,
                'spread_pct': None,
                'is_acceptable': True,
                'reason': 'no fill auditor — proceeding',
                'should_wait': False,
            }

        try:
            nbbo = self._fill_auditor.get_nbbo_now(symbol)
            if not nbbo or nbbo.get('mid', 0) <= 0:
                return {
                    'bid': None, 'ask': None, 'mid': None,
                    'spread_pct': None,
                    'is_acceptable': True,
                    'reason': 'no quote data — proceeding',
                    'should_wait': False,
                }

            bid = nbbo['bid']
            ask = nbbo['ask']
            mid = nbbo['mid']
            spread = ask - bid
            spread_pct = spread / mid if mid > 0 else 0

            # Record this observation
            self.record_spread(symbol, spread_pct)

            # Check against max threshold
            if spread_pct > self.max_spread_pct:
                return {
                    'bid': bid, 'ask': ask, 'mid': mid,
                    'spread_pct': spread_pct,
                    'is_acceptable': False,
                    'reason': (f'spread {spread_pct:.4f} ({spread_pct*100:.2f}%) '
                               f'exceeds max {self.max_spread_pct*100:.1f}%'),
                    'should_wait': True,
                }

            # Check against historical median
            should_wait = False
            history = self._spread_history.get(symbol)
            if history and len(history) >= 5:
                sorted_hist = sorted(history)
                median = sorted_hist[len(sorted_hist) // 2]
                if median > 0 and spread_pct > median * 1.5:
                    should_wait = True

            reason = 'spread acceptable'
            if should_wait:
                reason = (f'spread {spread_pct:.4f} is >1.5x historical median — '
                          f'consider waiting')

            return {
                'bid': bid, 'ask': ask, 'mid': mid,
                'spread_pct': spread_pct,
                'is_acceptable': True,
                'reason': reason,
                'should_wait': should_wait,
            }

        except Exception as e:
            print(f"  [spread_checker] check_spread error for {symbol}: {e}")
            return {
                'bid': None, 'ask': None, 'mid': None,
                'spread_pct': None,
                'is_acceptable': True,
                'reason': f'error checking spread — proceeding: {e}',
                'should_wait': False,
            }

    def record_spread(self, symbol: str, spread_pct: float):
        """Record a spread observation for historical tracking."""
        self._spread_history[symbol].append(spread_pct)
