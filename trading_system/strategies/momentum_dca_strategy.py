"""
Momentum DCA Strategy
Ensures at least 20% of each position is covered by sell orders
within 8% of the current price.
If coverage is below threshold:
  - Price within 0.75% of existing order -> resubmit at original price
  - Price moved >0.75% -> place stop-limit at -1.25% below current price
    with paired buy at -$0.50 below stop

Parameters optimized via grid search (see backtests/parameter_optimizer.py):
  stop_offset_pct=1.25%, buy_offset=$0.50, coverage=20%

Uses Ticker/Order entities for order tracking (no raw dicts).
"""

from typing import Dict, Optional, List

from trading_system.config import DEFAULT_LOT_SIZE
from trading_system.entities.Ticker import Ticker
from trading_system.strategies.base_strategy import BaseStrategy


class MomentumDcaLongStrategy(BaseStrategy):
    """
    Momentum Dollar-Cost Averaging Strategy

    Monitors open sell orders (via Ticker) against positions and maintains a
    minimum coverage threshold. Places protective orders to fill gaps.
    """

    # Map source symbol -> hedge symbol for protective orders
    # BTC is Grayscale Bitcoin Mini Trust ETF, no remapping needed
    DEFAULT_HEDGE_MAP = {}

    def __init__(self, symbols: List[str], coverage_threshold: float = 0.20,
                 stop_offset_pct: float = 0.0125, proximity_pct: float = 0.0075,
                 coverage_range_pct: float = 0.08,
                 buy_offset: float = 0.50,
                 lot_size: int = DEFAULT_LOT_SIZE,
                 hedge_symbol_map: Dict = None):
        self.symbols = symbols
        self.coverage_threshold = coverage_threshold
        self.stop_offset_pct = stop_offset_pct
        self.proximity_pct = proximity_pct
        self.coverage_range_pct = coverage_range_pct
        self.buy_offset = buy_offset
        self.lot_size = lot_size
        self.hedge_symbol_map = hedge_symbol_map if hedge_symbol_map is not None else self.DEFAULT_HEDGE_MAP

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
        signal_orders = ticker.get_signal_orders()

        # Only count orders within coverage_range_pct of current price
        in_range = [o for o in signal_orders
                    if o.price and abs(current_price - o.price) / current_price <= self.coverage_range_pct]
        out_of_range = [o for o in signal_orders if o not in in_range]

        covered_qty = sum(o.size for o in in_range)
        coverage_pct = (covered_qty / position_qty) * 100 if position_qty > 0 else 0

        if coverage_pct >= self.coverage_threshold * 100:
            return {
                'signal': 'COVERED',
                'reason': (f'{symbol}: {coverage_pct:.1f}% covered '
                           f'({covered_qty:.4f}/{position_qty:.4f}), '
                           f'threshold {self.coverage_threshold * 100}%'),
                'order': None,
                'position_qty': position_qty,
                'covered_qty': covered_qty,
                'coverage_pct': coverage_pct,
                'existing_orders': in_range,
                'out_of_range_orders': out_of_range,
                'current_price': current_price,
            }

        # Under-covered — calculate gap, capped to lot_size
        gap_qty = (self.coverage_threshold * position_qty) - covered_qty
        gap_qty = self._round_quantity(symbol, gap_qty)
        gap_qty = min(gap_qty, self.lot_size)

        if gap_qty <= 0:
            return {'signal': 'COVERED', 'reason': f'{symbol}: gap rounds to zero', 'order': None}

        # Resolve hedge symbol for protective orders
        order_symbol = self.hedge_symbol_map.get(symbol, symbol)

        # Check price proximity to nearest existing sell order
        nearest = self._find_nearest_order(current_price, signal_orders)

        if nearest:
            order_price = nearest.price
            if order_price and self._is_within_proximity(current_price, order_price):
                return {
                    'signal': 'RESUBMIT',
                    'reason': (f'{symbol}: {coverage_pct:.1f}% covered, '
                               f'price ${current_price:.2f} within {self.proximity_pct * 100}% '
                               f'of order @ ${order_price:.2f}. '
                               f'Resubmitting {gap_qty} shares of {order_symbol} at ${order_price:.2f}'),
                    'order': {
                        'action': 'limit_sell',
                        'symbol': order_symbol,
                        'quantity': gap_qty,
                        'price': order_price,
                        'current_price': current_price,
                    }
                }

        # Price moved too far — new stop-limit
        stop_price = round(current_price * (1 - self.stop_offset_pct), 2)
        buy_price = round(stop_price - self.buy_offset, 2)

        target_qty = self._round_quantity(symbol, self.coverage_threshold * position_qty)

        return {
            'signal': 'COVER_GAP',
            'reason': (f'{symbol}: {coverage_pct:.1f}% covered, '
                       f'need {gap_qty} more shares protected. '
                       f'Placing stop-limit sell on {order_symbol} @ ${stop_price:.2f} '
                       f'(-{self.stop_offset_pct * 100}%) '
                       f'with paired buy @ ${buy_price:.2f}'),
            'order': {
                'action': 'stop_limit_sell',
                'symbol': order_symbol,
                'quantity': gap_qty,
                'stop_price': stop_price,
                'limit_price': stop_price,
                'current_price': current_price,
            },
            'paired_buy': {
                'action': 'limit_buy',
                'symbol': order_symbol,
                'quantity': gap_qty,
                'price': buy_price,
                'current_price': current_price,
            },
            'position_qty': position_qty,
            'target_qty': target_qty,
            'covered_qty': covered_qty,
            'coverage_pct': coverage_pct,
            'gap_qty': gap_qty,
        }

    def _find_nearest_order(self, current_price, signal_orders):
        """Find the Order entity whose price is closest to current price"""
        best = None
        best_dist = float('inf')
        for order in signal_orders:
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
        return int(quantity)

    def calculate_position_size(self, symbol, price, available_cash):
        return int(available_cash * 0.25 / price)

    def format_signal(self, symbol, signal_data):
        lines = [
            f"\n{'='*70}",
            f"MOMENTUM DCA: {symbol}",
            f"{'='*70}",
            f"Signal: {signal_data['signal']}",
            f"Reason: {signal_data['reason']}",
        ]
        if signal_data['signal'] == 'COVERED' and signal_data.get('existing_orders'):
            orders = signal_data['existing_orders']
            out_of_range = signal_data.get('out_of_range_orders', [])
            position_qty = signal_data['position_qty']
            covered_qty = signal_data['covered_qty']
            coverage_pct = signal_data['coverage_pct']
            current_price = signal_data['current_price']
            lines.append("")
            lines.append(f"Coverage Breakdown:")
            lines.append(f"  Position:  {position_qty:,.4f} units @ ${current_price:,.2f}")
            lines.append(f"  Threshold: {self.coverage_threshold * 100:.0f}% "
                         f"({self.coverage_threshold * position_qty:,.4f} units needed)")
            lines.append(f"  Covered:   {covered_qty:,.4f} units = {coverage_pct:.1f}% "
                         f"(orders within {self.coverage_range_pct * 100:.0f}% of price)")
            lines.append("")
            lines.append(f"  Sell Orders Within Range ({len(orders)}):")
            for i, o in enumerate(orders, 1):
                pct_away = abs(current_price - o.price) / current_price * 100
                lines.append(f"    {i}. {o.size:,.4f} units @ ${o.price:,.2f} "
                             f"({o.order_type.value}) [{pct_away:+.1f}%]")
            if out_of_range:
                lines.append("")
                lines.append(f"  Out of Range ({len(out_of_range)}) — not counted:")
                for i, o in enumerate(out_of_range, 1):
                    pct_away = abs(current_price - o.price) / current_price * 100
                    lines.append(f"    {i}. {o.size:,.4f} units @ ${o.price:,.2f} "
                                 f"({o.order_type.value}) [{pct_away:+.1f}%]")
        if signal_data.get('position_qty') and signal_data['signal'] == 'COVER_GAP':
            lines.append("")
            lines.append("Coverage Sizing:")
            lines.append(f"  Position:  {signal_data['position_qty']:,.0f} shares")
            lines.append(f"  Target:    {signal_data['target_qty']:,.0f} shares "
                         f"({self.coverage_threshold * 100:.0f}% of position)")
            lines.append(f"  Covered:   {signal_data['covered_qty']:,.0f} shares "
                         f"({signal_data['coverage_pct']:.1f}%)")
            lines.append(f"  Gap:       {signal_data['gap_qty']:,.0f} shares "
                         f"(lot cap: {self.lot_size})")

        if signal_data['order']:
            order = signal_data['order']
            lines.append("")
            lines.append("Order Details:")
            lines.append(f"  Action: {order['action'].upper()}")
            lines.append(f"  Symbol: {order['symbol']}")
            if order['symbol'] != symbol:
                lines.append(f"  (hedging {symbol} via {order['symbol']})")
            lines.append(f"  Quantity: {order['quantity']}")
            lines.append(f"  Current Price: ${order['current_price']:,.2f}")
            if order['action'] == 'stop_limit_sell':
                lines.append(f"  Stop Price: ${order['stop_price']:,.2f}")
                lines.append(f"  Limit Price: ${order['limit_price']:,.2f}")
            elif order['action'] == 'limit_sell':
                lines.append(f"  Limit Price: ${order['price']:,.2f}")
        if signal_data.get('paired_buy'):
            buy = signal_data['paired_buy']
            lines.append("")
            lines.append("Paired Buy Order:")
            lines.append(f"  Action: LIMIT_BUY")
            lines.append(f"  Symbol: {buy['symbol']}")
            lines.append(f"  Quantity: {buy['quantity']}")
            lines.append(f"  Limit Price: ${buy['price']:,.2f} "
                         f"(-${self.buy_offset:.2f} below stop)")
        lines.append(f"{'='*70}\n")
        return '\n'.join(lines)
