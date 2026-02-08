"""
Momentum DCA Strategy
Ensures at least 20% of each position is covered by sell orders at all times.
If coverage is below threshold:
  - Price within 0.75% of existing order -> resubmit at original price
  - Price moved >0.75% -> place stop-limit at -1.5% below current price

Uses Ticker/Order entities for order tracking (no raw dicts).
"""

from typing import Dict, Optional, List

from trading_system.entities.Ticker import Ticker


class MomentumDcaStrategy:
    """
    Momentum Dollar-Cost Averaging Strategy

    Monitors open sell orders (via Ticker) against positions and maintains a
    minimum coverage threshold. Places protective orders to fill gaps.
    """

    def __init__(self, symbols: List[str], coverage_threshold: float = 0.20,
                 stop_offset_pct: float = 0.015, proximity_pct: float = 0.0075):
        self.symbols = symbols
        self.coverage_threshold = coverage_threshold
        self.stop_offset_pct = stop_offset_pct
        self.proximity_pct = proximity_pct

    def analyze_symbol(self, symbol: str, metrics: Dict,
                       current_position: Optional[Dict],
                       ticker: Ticker) -> Dict:
        """
        Analyze coverage for a symbol and generate signal.

        Args:
            symbol: Stock symbol
            metrics: Must include 'current_price'
            current_position: Broker position dict (or None)
            ticker: Ticker loaded with the symbol's open sell orders
        """
        current_price = metrics.get('current_price', 0)
        if not current_price:
            return {'signal': 'NO_DATA', 'reason': 'No current price available', 'order': None}

        if not current_position or float(current_position.get('quantity', 0)) <= 0:
            return {'signal': 'NO_POSITION', 'reason': f'No position in {symbol}', 'order': None}

        position_qty = float(current_position['quantity'])
        valid_orders = ticker.get_valid_orders()
        covered_qty = sum(o.size for o in valid_orders)
        coverage_pct = (covered_qty / position_qty) * 100 if position_qty > 0 else 0

        if coverage_pct >= self.coverage_threshold * 100:
            return {
                'signal': 'COVERED',
                'reason': (f'{symbol}: {coverage_pct:.1f}% covered '
                           f'({covered_qty:.4f}/{position_qty:.4f}), '
                           f'threshold {self.coverage_threshold * 100}%'),
                'order': None
            }

        # Under-covered — calculate gap
        gap_qty = (self.coverage_threshold * position_qty) - covered_qty
        gap_qty = self._round_quantity(symbol, gap_qty)

        if gap_qty <= 0:
            return {'signal': 'COVERED', 'reason': f'{symbol}: gap rounds to zero', 'order': None}

        # Check price proximity to nearest existing sell order
        nearest = self._find_nearest_order(current_price, valid_orders)

        if nearest:
            order_price = nearest.price
            if order_price and self._is_within_proximity(current_price, order_price):
                return {
                    'signal': 'RESUBMIT',
                    'reason': (f'{symbol}: {coverage_pct:.1f}% covered, '
                               f'price ${current_price:.2f} within {self.proximity_pct * 100}% '
                               f'of order @ ${order_price:.2f}. '
                               f'Resubmitting {gap_qty} shares at ${order_price:.2f}'),
                    'order': {
                        'action': 'limit_sell',
                        'symbol': symbol,
                        'quantity': gap_qty,
                        'price': order_price,
                        'current_price': current_price,
                    }
                }

        # Price moved too far — new stop-limit
        stop_price = round(current_price * (1 - self.stop_offset_pct), 2)

        return {
            'signal': 'COVER_GAP',
            'reason': (f'{symbol}: {coverage_pct:.1f}% covered, '
                       f'need {gap_qty} more shares protected. '
                       f'Placing stop-limit sell @ ${stop_price:.2f} '
                       f'(-{self.stop_offset_pct * 100}%)'),
            'order': {
                'action': 'stop_limit_sell',
                'symbol': symbol,
                'quantity': gap_qty,
                'stop_price': stop_price,
                'limit_price': stop_price,
                'current_price': current_price,
            }
        }

    def _find_nearest_order(self, current_price, valid_orders):
        """Find the Order entity whose price is closest to current price"""
        best = None
        best_dist = float('inf')
        for order in valid_orders:
            if not order.price:
                continue
            dist = abs(current_price - order.price) / order.price
            if dist < best_dist:
                best_dist = dist
                best = order
        return best

    def _is_within_proximity(self, current_price, order_price):
        return abs(current_price - order_price) / order_price <= self.proximity_pct

    def _round_quantity(self, symbol, quantity):
        if symbol == 'BTC':
            return round(quantity, 4)
        return int(quantity)

    def calculate_position_size(self, symbol, price, available_cash):
        if symbol == 'BTC':
            return round(available_cash * 0.25 / price, 4)
        return int(available_cash * 0.25 / price)

    def format_signal(self, symbol, signal_data):
        lines = [
            f"\n{'='*70}",
            f"MOMENTUM DCA: {symbol}",
            f"{'='*70}",
            f"Signal: {signal_data['signal']}",
            f"Reason: {signal_data['reason']}",
        ]
        if signal_data['order']:
            order = signal_data['order']
            lines.append("")
            lines.append("Order Details:")
            lines.append(f"  Action: {order['action'].upper()}")
            lines.append(f"  Quantity: {order['quantity']}")
            lines.append(f"  Current Price: ${order['current_price']:,.2f}")
            if order['action'] == 'stop_limit_sell':
                lines.append(f"  Stop Price: ${order['stop_price']:,.2f}")
                lines.append(f"  Limit Price: ${order['limit_price']:,.2f}")
            elif order['action'] == 'limit_sell':
                lines.append(f"  Limit Price: ${order['price']:,.2f}")
        lines.append(f"{'='*70}\n")
        return '\n'.join(lines)
