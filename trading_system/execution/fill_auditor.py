"""
Fill Auditor — cross-references Robinhood fills against independent NBBO data.

Fallback chain: Redis (Scala market-data-service) → Alpaca API → TwelveData last-price.
"""

import json
import os
import tempfile
from datetime import datetime

try:
    from alpaca.data.historical import StockHistoricalDataClient
    from alpaca.data.requests import StockLatestQuoteRequest
    HAS_ALPACA = True
except ImportError:
    HAS_ALPACA = False


class FillAuditor:
    """Audits fill quality by comparing Robinhood fills against independent NBBO data."""

    def __init__(self, alpaca_key: str = '', alpaca_secret: str = '',
                 twelve_data_provider=None):
        self._alpaca_client = None
        self._twelve_data_provider = twelve_data_provider
        self._audit_log_path = os.path.join(
            os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
            'fill_audit_log.json'
        )

        if HAS_ALPACA and alpaca_key and alpaca_secret:
            try:
                self._alpaca_client = StockHistoricalDataClient(alpaca_key, alpaca_secret)
            except Exception as e:
                print(f"  [fill_auditor] Alpaca client init failed: {e}")
                self._alpaca_client = None

    def _get_nbbo_from_redis(self, symbol: str) -> dict:
        """Read the latest quote from Redis (populated by the Scala market-data-service)."""
        try:
            from trading_system.state.redis_store import _get_client
            client = _get_client()
            if not client:
                return None
            raw = client.hget("market-quotes", symbol)
            client.close()
            if raw:
                data = json.loads(raw)
                return {
                    'bid': data['bid'],
                    'ask': data['ask'],
                    'mid': data['mid'],
                    'spread': data['spread'],
                    'spread_bps': data['spread_bps'],
                    'bid_exchange': data.get('bid_exchange'),
                    'ask_exchange': data.get('ask_exchange'),
                    'timestamp': data.get('timestamp', datetime.utcnow().isoformat()),
                    'source': 'redis_alpaca',
                }
        except Exception as e:
            print(f"  [fill_auditor] Redis quote read failed for {symbol}: {e}")
        return None

    def get_nbbo_now(self, symbol: str) -> dict:
        """Get current NBBO quote. Fallback chain: Redis → Alpaca API → TwelveData.

        Returns:
            Dict with bid, ask, mid, spread, spread_bps, etc. or None on failure.
        """
        # Try Redis first (fastest — no API call, populated by Scala service)
        redis_quote = self._get_nbbo_from_redis(symbol)
        if redis_quote:
            return redis_quote

        # Try Alpaca API directly
        if self._alpaca_client:
            try:
                request = StockLatestQuoteRequest(symbol_or_symbols=symbol)
                quotes = self._alpaca_client.get_stock_latest_quote(request)
                quote = quotes.get(symbol) if isinstance(quotes, dict) else quotes
                if quote:
                    bid = float(quote.bid_price)
                    ask = float(quote.ask_price)
                    mid = (bid + ask) / 2.0
                    spread = ask - bid
                    spread_bps = (spread / mid * 10000) if mid > 0 else 0
                    return {
                        'bid': bid,
                        'ask': ask,
                        'mid': mid,
                        'spread': spread,
                        'spread_bps': spread_bps,
                        'bid_exchange': getattr(quote, 'bid_exchange', None),
                        'ask_exchange': getattr(quote, 'ask_exchange', None),
                        'timestamp': datetime.utcnow().isoformat(),
                        'source': 'alpaca',
                    }
            except Exception as e:
                print(f"  [fill_auditor] Alpaca quote failed for {symbol}: {e}")

        # Fallback to TwelveData last price
        if self._twelve_data_provider:
            try:
                price_data = self._twelve_data_provider.get_realtime_price(symbol)
                if price_data and isinstance(price_data, dict):
                    price = float(price_data.get('price', 0))
                    if price > 0:
                        return {
                            'bid': price,
                            'ask': price,
                            'mid': price,
                            'spread': 0,
                            'spread_bps': 0,
                            'bid_exchange': None,
                            'ask_exchange': None,
                            'timestamp': datetime.utcnow().isoformat(),
                            'source': 'twelve_data_last',
                        }
            except Exception as e:
                print(f"  [fill_auditor] TwelveData fallback failed for {symbol}: {e}")

        return None

    def audit_fill(self, symbol, side, fill_price, fill_quantity,
                   rh_order_id=None) -> dict:
        """Audit a fill by comparing against current NBBO.

        Call immediately after a Robinhood fill confirmation.

        Returns:
            Audit dict with slippage metrics, or None on failure.
        """
        try:
            nbbo = self.get_nbbo_now(symbol)
            if not nbbo:
                return None

            bid = nbbo['bid']
            ask = nbbo['ask']
            mid = nbbo['mid']

            # Compute slippage vs mid
            if side.lower() == 'buy':
                slippage_vs_mid = fill_price - mid  # positive = worse for buyer
                nbbo_edge = bid  # best case for buyer
            else:
                slippage_vs_mid = mid - fill_price  # positive = worse for seller
                nbbo_edge = ask  # best case for seller

            slippage_vs_mid_bps = (slippage_vs_mid / mid * 10000) if mid > 0 else 0
            cost_impact = slippage_vs_mid * fill_quantity

            # Grade
            abs_bps = abs(slippage_vs_mid_bps)
            if abs_bps <= 5:
                grade = 'GOOD'
            elif abs_bps <= 20:
                grade = 'OK'
            else:
                grade = 'BAD'

            audit = {
                'timestamp': datetime.utcnow().isoformat(),
                'symbol': symbol,
                'side': side,
                'fill_price': fill_price,
                'fill_quantity': fill_quantity,
                'rh_order_id': rh_order_id,
                'nbbo_bid': bid,
                'nbbo_ask': ask,
                'nbbo_mid': mid,
                'slippage_vs_mid': round(slippage_vs_mid, 6),
                'slippage_vs_mid_bps': round(slippage_vs_mid_bps, 2),
                'cost_impact': round(cost_impact, 4),
                'grade': grade,
                'data_source': nbbo.get('source', 'unknown'),
            }

            self.record_audit(audit)
            return audit

        except Exception as e:
            print(f"  [fill_auditor] audit_fill error: {e}")
            return None

    def record_audit(self, audit: dict):
        """Append audit record to fill_audit_log.json atomically."""
        try:
            records = []
            if os.path.exists(self._audit_log_path):
                with open(self._audit_log_path, 'r') as f:
                    records = json.load(f)

            records.append(audit)

            # Atomic write
            dir_name = os.path.dirname(self._audit_log_path)
            fd, tmp_path = tempfile.mkstemp(dir=dir_name, suffix='.tmp')
            try:
                with os.fdopen(fd, 'w') as f:
                    json.dump(records, f, indent=2)
                os.replace(tmp_path, self._audit_log_path)
            except Exception:
                if os.path.exists(tmp_path):
                    os.unlink(tmp_path)
                raise

        except Exception as e:
            print(f"  [fill_auditor] record_audit error: {e}")

    def summary(self) -> dict:
        """Return aggregate stats from the audit log."""
        try:
            if not os.path.exists(self._audit_log_path):
                return {}

            with open(self._audit_log_path, 'r') as f:
                records = json.load(f)

            if not records:
                return {}

            total = len(records)
            slippages = [r['slippage_vs_mid_bps'] for r in records if 'slippage_vs_mid_bps' in r]
            costs = [r['cost_impact'] for r in records if 'cost_impact' in r]
            better = sum(1 for s in slippages if s < 0)

            return {
                'total_fills': total,
                'avg_slippage_bps': round(sum(slippages) / len(slippages), 2) if slippages else 0,
                'total_slippage_cost': round(sum(costs), 2) if costs else 0,
                'pct_better_than_mid': round(better / total * 100, 1) if total > 0 else 0,
                'worst_fill_bps': round(max(slippages), 2) if slippages else 0,
            }

        except Exception as e:
            print(f"  [fill_auditor] summary error: {e}")
            return {}
