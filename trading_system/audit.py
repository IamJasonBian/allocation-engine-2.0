"""
Trading System Audit Module
Checks coverage of different order types against open positions
"""

import sys
import os
from typing import Dict, List, Tuple

# Add parent directory to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from utils.safe_cash_bot import SafeCashBot  # noqa: E402


class StopLossAuditor:
    """Audits order coverage for portfolio positions"""

    def __init__(self):
        """Initialize the auditor with trading bot"""
        self.bot = SafeCashBot()

    def get_positions_and_orders(self) -> Tuple[List[Dict], List[Dict]]:
        """
        Get current positions and open orders

        Returns:
            Tuple of (positions, orders)
        """
        positions = self.bot.get_positions()
        orders = self.bot.get_open_orders()
        return positions, orders

    def find_orders_for_position(self, symbol: str, orders: List[Dict]) -> Dict[str, List[Dict]]:
        """
        Find all SELL orders for a given position, categorized by type

        Args:
            symbol: Stock symbol
            orders: List of open orders

        Returns:
            Dictionary with order types as keys and lists of matching orders
        """
        order_map = {
            'limit': [],
            'stop': [],
            'stop_limit': [],
            'trailing_stop': []
        }

        for order in orders:
            if order['symbol'] == symbol and order['side'] == 'SELL':
                order_type = order['order_type']
                trigger = order.get('trigger', 'immediate')

                # Categorize orders based on type and trigger
                if order_type == 'Limit' and trigger == 'immediate':
                    order_map['limit'].append(order)
                elif order_type == 'Stop Loss' and trigger == 'stop':
                    order_map['stop'].append(order)
                elif order_type == 'Stop Limit' and trigger == 'stop':
                    order_map['stop_limit'].append(order)
                # Note: Robinhood API may not explicitly expose trailing stops
                # They might appear as regular stop orders
                # Check if there's a trailing_pct or similar field
                elif 'trailing' in order_type.lower():
                    order_map['trailing_stop'].append(order)

        return order_map

    def check_coverage(self, filter_symbol: str = None) -> Dict:
        """
        Check order coverage for all positions by type

        Args:
            filter_symbol: Optional symbol to filter analysis to a specific position

        Returns:
            Dictionary with coverage analysis including breakdown by order type
        """
        positions, orders = self.get_positions_and_orders()

        # Filter positions if symbol specified
        if filter_symbol:
            positions = [p for p in positions if p['symbol'] == filter_symbol]
            if not positions:
                print(f"⚠️  No position found for symbol: {filter_symbol}")
                return {
                    'total_positions': 0,
                    'total_equity': 0.0,
                    'coverage_by_type': {
                        'limit': {'quantity': 0, 'equity': 0.0, 'pct': 0.0},
                        'stop': {'quantity': 0, 'equity': 0.0, 'pct': 0.0},
                        'stop_limit': {'quantity': 0, 'equity': 0.0, 'pct': 0.0},
                        'trailing_stop': {'quantity': 0, 'equity': 0.0, 'pct': 0.0},
                        'any_protection': {'quantity': 0, 'equity': 0.0, 'pct': 0.0}
                    },
                    'details': [],
                    'largest_uncovered': None
                }

        if not positions:
            return {
                'total_positions': 0,
                'total_equity': 0.0,
                'coverage_by_type': {
                    'limit': {'quantity': 0, 'equity': 0.0, 'pct': 0.0},
                    'stop': {'quantity': 0, 'equity': 0.0, 'pct': 0.0},
                    'stop_limit': {'quantity': 0, 'equity': 0.0, 'pct': 0.0},
                    'trailing_stop': {'quantity': 0, 'equity': 0.0, 'pct': 0.0},
                    'any_protection': {'quantity': 0, 'equity': 0.0, 'pct': 0.0}
                },
                'details': [],
                'largest_uncovered': None
            }

        # Calculate total equity across all positions
        total_equity = sum(pos['equity'] for pos in positions)

        # Analyze each position
        details = []
        coverage_equity = {
            'limit': 0.0,
            'stop': 0.0,
            'stop_limit': 0.0,
            'trailing_stop': 0.0,
            'any_protection': 0.0
        }

        for pos in positions:
            symbol = pos['symbol']
            position_orders = self.find_orders_for_position(symbol, orders)

            # Check what types of orders exist
            has_limit = len(position_orders['limit']) > 0
            has_stop = len(position_orders['stop']) > 0
            has_stop_limit = len(position_orders['stop_limit']) > 0
            has_trailing_stop = len(position_orders['trailing_stop']) > 0
            has_any = has_limit or has_stop or has_stop_limit or has_trailing_stop

            # Calculate covered quantity for each order type
            limit_qty = sum(o['quantity'] for o in position_orders['limit'])
            stop_qty = sum(o['quantity'] for o in position_orders['stop'])
            stop_limit_qty = sum(o['quantity'] for o in position_orders['stop_limit'])
            trailing_stop_qty = sum(o['quantity'] for o in position_orders['trailing_stop'])

            position_detail = {
                'symbol': symbol,
                'quantity': pos['quantity'],
                'current_price': pos['current_price'],
                'equity': pos['equity'],
                'order_coverage': {
                    'limit': {
                        'has': has_limit,
                        'quantity': limit_qty,
                        'pct': (limit_qty / pos['quantity'] * 100) if pos['quantity'] > 0 else 0,
                        'orders': position_orders['limit']
                    },
                    'stop': {
                        'has': has_stop,
                        'quantity': stop_qty,
                        'pct': (stop_qty / pos['quantity'] * 100) if pos['quantity'] > 0 else 0,
                        'orders': position_orders['stop']
                    },
                    'stop_limit': {
                        'has': has_stop_limit,
                        'quantity': stop_limit_qty,
                        'pct': (stop_limit_qty / pos['quantity'] * 100) if pos['quantity'] > 0 else 0,
                        'orders': position_orders['stop_limit']
                    },
                    'trailing_stop': {
                        'has': has_trailing_stop,
                        'quantity': trailing_stop_qty,
                        'pct': (trailing_stop_qty / pos['quantity'] * 100) if pos['quantity'] > 0 else 0,
                        'orders': position_orders['trailing_stop']
                    }
                },
                'has_any_protection': has_any
            }

            # Accumulate equity coverage
            if has_limit:
                coverage_equity['limit'] += pos['equity']
            if has_stop:
                coverage_equity['stop'] += pos['equity']
            if has_stop_limit:
                coverage_equity['stop_limit'] += pos['equity']
            if has_trailing_stop:
                coverage_equity['trailing_stop'] += pos['equity']
            if has_any:
                coverage_equity['any_protection'] += pos['equity']

            details.append(position_detail)

        # Sort by equity descending
        details.sort(key=lambda x: x['equity'], reverse=True)

        # Find largest uncovered position
        uncovered = [d for d in details if not d['has_any_protection']]
        largest_uncovered = uncovered[0] if uncovered else None

        # Calculate percentages
        coverage_by_type = {}
        for order_type in ['limit', 'stop', 'stop_limit', 'trailing_stop', 'any_protection']:
            positions_with_type = sum(
                1 for d in details
                if (order_type == 'any_protection' and d['has_any_protection'])
                or (order_type != 'any_protection' and d['order_coverage'][order_type]['has'])
            )
            coverage_by_type[order_type] = {
                'positions': positions_with_type,
                'equity': coverage_equity[order_type],
                'pct_positions': (positions_with_type / len(positions) * 100) if positions else 0,
                'pct_equity': (coverage_equity[order_type] / total_equity * 100) if total_equity > 0 else 0
            }

        return {
            'total_positions': len(positions),
            'total_equity': total_equity,
            'coverage_by_type': coverage_by_type,
            'details': details,
            'largest_uncovered': largest_uncovered
        }

    def print_coverage_report(self, coverage: Dict):
        """
        Print formatted coverage report with order type breakdown

        Args:
            coverage: Coverage analysis dictionary
        """
        print(f"\n{'='*80}")
        print("ORDER COVERAGE AUDIT")
        print(f"{'='*80}\n")

        # Summary
        print(f"📊 Portfolio Summary:")
        print(f"   Total Positions: {coverage['total_positions']}")
        print(f"   Total Equity: ${coverage['total_equity']:,.2f}")

        if coverage['total_positions'] == 0:
            print("\n   No positions to audit")
            print(f"\n{'='*80}\n")
            return

        # Coverage by order type
        print(f"\n📋 Order Coverage by Type:\n")

        order_type_labels = {
            'limit': 'Limit Orders',
            'stop': 'Stop Orders',
            'stop_limit': 'Stop Limit Orders',
            'trailing_stop': 'Trailing Stop Orders',
            'any_protection': 'Any Protection'
        }

        for order_type, label in order_type_labels.items():
            cov = coverage['coverage_by_type'][order_type]
            print(f"   {label}:")
            print(f"      Positions Covered: {cov['positions']}/{coverage['total_positions']} ({cov['pct_positions']:.1f}%)")
            print(f"      Equity Covered: ${cov['equity']:,.2f} ({cov['pct_equity']:.1f}% of total)")
            print()

        # Position details
        print(f"\n📈 Position Details (sorted by size):\n")

        for i, pos in enumerate(coverage['details'], 1):
            status_icon = "✅" if pos['has_any_protection'] else "❌"
            print(f"   {i}. {pos['symbol']}")
            print(f"      Equity: ${pos['equity']:,.2f}")
            print(f"      Quantity: {pos['quantity']:.0f} @ ${pos['current_price']:.2f}")
            print(f"      Protected: {status_icon} {'YES' if pos['has_any_protection'] else 'NO'}")

            # Show order coverage breakdown
            if pos['has_any_protection']:
                print(f"      Order Coverage:")
                for order_type in ['limit', 'stop', 'stop_limit', 'trailing_stop']:
                    cov = pos['order_coverage'][order_type]
                    if cov['has']:
                        type_label = order_type.replace('_', ' ').title()
                        print(f"         • {type_label}: {cov['quantity']:.0f} shares ({cov['pct']:.1f}%)")

                        # Show order details
                        for order in cov['orders']:
                            if order.get('limit_price'):
                                print(f"            - Limit @ ${order['limit_price']:.2f}")
                            if order.get('stop_price'):
                                stop_pct = ((order['stop_price'] - pos['current_price']) / pos['current_price']) * 100
                                print(f"            - Stop @ ${order['stop_price']:.2f} ({stop_pct:+.1f}%)")

            print()

        # Alert for largest uncovered position
        if coverage['largest_uncovered']:
            largest = coverage['largest_uncovered']
            print(f"⚠️  ALERT: Largest Uncovered Position")
            print(f"   Symbol: {largest['symbol']}")
            print(f"   Equity: ${largest['equity']:,.2f}")
            print(f"   Quantity: {largest['quantity']:.0f}")
            print(f"   Current Price: ${largest['current_price']:.2f}")
            print(f"\n   Recommendation: Set stop loss for {largest['symbol']}")
            print(f"   Example (5% stop): ${largest['current_price'] * 0.95:.2f}")
            print(f"   Example (10% stop): ${largest['current_price'] * 0.90:.2f}")
        else:
            print(f"✅ All positions have protection!")

        print(f"\n{'='*80}\n")

    def run_audit(self, filter_symbol: str = None):
        """
        Run the order coverage audit

        Args:
            filter_symbol: Optional symbol to filter analysis to a specific position
        """
        try:
            coverage = self.check_coverage(filter_symbol)
            self.print_coverage_report(coverage)

            # Return exit code based on coverage
            if coverage['largest_uncovered']:
                return 1  # Exit with error if largest position uncovered
            return 0

        except Exception as e:
            print(f"❌ Error running audit: {e}")
            import traceback
            traceback.print_exc()
            return 1
        finally:
            self.bot.auth.logout()


def main():
    """Main entry point for order coverage audit"""
    import argparse

    parser = argparse.ArgumentParser(description='Audit order coverage for portfolio positions')
    parser.add_argument(
        '--symbol',
        type=str,
        help='Filter audit to a specific symbol (e.g., BTC, AAPL)'
    )
    args = parser.parse_args()

    print("\n🔍 Order Coverage Audit")
    if args.symbol:
        print(f"📌 Filtering to symbol: {args.symbol}")
    print(f"⏰ {__import__('datetime').datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")

    auditor = StopLossAuditor()
    exit_code = auditor.run_audit(filter_symbol=args.symbol)

    if exit_code == 1:
        print("⚠️  Action Required: Set protection for largest position")
    else:
        print("✅ Audit Complete: Portfolio properly protected")

    sys.exit(exit_code)


if __name__ == "__main__":
    main()
