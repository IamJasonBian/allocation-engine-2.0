"""
State Management Module
Stores and manages metrics and order state for each symbol
"""

import json
import os
from datetime import datetime
from typing import Dict, List, Optional


class StateManager:
    """Manages persistent state for trading system"""

    def __init__(self, state_file: str = 'trading_state.json'):
        """
        Initialize state manager

        Args:
            state_file: Path to state file
        """
        self.state_file = state_file
        self.state = self._load_state()

    def _load_state(self) -> Dict:
        """Load state from file"""
        if os.path.exists(self.state_file):
            try:
                with open(self.state_file, 'r') as f:
                    return json.load(f)
            except Exception as e:
                print(f"Error loading state: {e}")
                return self._create_empty_state()
        else:
            return self._create_empty_state()

    def _create_empty_state(self) -> Dict:
        """Create empty state structure"""
        return {
            'symbols': {},
            'last_updated': None
        }

    def _save_state(self):
        """Save state to file"""
        try:
            # Update last modified time
            self.state['last_updated'] = datetime.now().isoformat()

            # Write to file with pretty formatting
            with open(self.state_file, 'w') as f:
                json.dump(self.state, f, indent=2)
        except Exception as e:
            print(f"Error saving state: {e}")

    def get_symbol_state(self, symbol: str) -> Dict:
        """
        Get state for a specific symbol

        Args:
            symbol: Stock symbol

        Returns:
            Symbol state dictionary
        """
        if symbol not in self.state['symbols']:
            self.state['symbols'][symbol] = {
                'metrics': {},
                'orders': {
                    'active_buy': None,
                    'active_sell': None,
                    'order_history': []
                },
                'last_signal': None,
                'last_updated': None
            }

        return self.state['symbols'][symbol]

    def update_metrics(self, symbol: str, metrics: Dict):
        """
        Update metrics for a symbol

        Args:
            symbol: Stock symbol
            metrics: Metrics dictionary
        """
        symbol_state = self.get_symbol_state(symbol)
        symbol_state['metrics'] = metrics
        symbol_state['last_updated'] = datetime.now().isoformat()
        self._save_state()

    def get_metrics(self, symbol: str) -> Dict:
        """
        Get current metrics for a symbol

        Args:
            symbol: Stock symbol

        Returns:
            Metrics dictionary
        """
        return self.get_symbol_state(symbol).get('metrics', {})

    def queue_buy_order(self, symbol: str, order_details: Dict):
        """
        Queue a buy order for a symbol

        Args:
            symbol: Stock symbol
            order_details: Order details (quantity, price, etc.)
        """
        symbol_state = self.get_symbol_state(symbol)

        order = {
            'type': 'buy',
            'status': 'queued',
            'queued_at': datetime.now().isoformat(),
            'details': order_details
        }

        symbol_state['orders']['active_buy'] = order
        symbol_state['orders']['order_history'].append(order.copy())
        self._save_state()

    def queue_sell_order(self, symbol: str, order_details: Dict):
        """
        Queue a sell order for a symbol

        Args:
            symbol: Stock symbol
            order_details: Order details (quantity, price, etc.)
        """
        symbol_state = self.get_symbol_state(symbol)

        order = {
            'type': 'sell',
            'status': 'queued',
            'queued_at': datetime.now().isoformat(),
            'details': order_details
        }

        symbol_state['orders']['active_sell'] = order
        symbol_state['orders']['order_history'].append(order.copy())
        self._save_state()

    def update_order_status(self, symbol: str, order_type: str, status: str,
                            order_id: Optional[str] = None):
        """
        Update order status

        Args:
            symbol: Stock symbol
            order_type: 'buy' or 'sell'
            status: New status ('queued', 'placed', 'filled', 'cancelled')
            order_id: Optional order ID from broker
        """
        symbol_state = self.get_symbol_state(symbol)

        order_key = f'active_{order_type}'
        if symbol_state['orders'][order_key]:
            symbol_state['orders'][order_key]['status'] = status
            symbol_state['orders'][order_key]['last_updated'] = datetime.now().isoformat()

            if order_id:
                symbol_state['orders'][order_key]['order_id'] = order_id

            # If filled or cancelled, clear active order
            if status in ['filled', 'cancelled']:
                symbol_state['orders'][order_key] = None

            self._save_state()

    def get_active_orders(self, symbol: str) -> Dict:
        """
        Get active orders for a symbol

        Args:
            symbol: Stock symbol

        Returns:
            Dictionary with 'buy' and 'sell' orders
        """
        symbol_state = self.get_symbol_state(symbol)
        return {
            'buy': symbol_state['orders']['active_buy'],
            'sell': symbol_state['orders']['active_sell']
        }

    def get_order_history(self, symbol: str, limit: int = 10) -> List[Dict]:
        """
        Get order history for a symbol

        Args:
            symbol: Stock symbol
            limit: Maximum number of orders to return

        Returns:
            List of historical orders
        """
        symbol_state = self.get_symbol_state(symbol)
        history = symbol_state['orders']['order_history']
        return history[-limit:] if history else []

    def set_last_signal(self, symbol: str, signal: str):
        """
        Record last trading signal

        Args:
            symbol: Stock symbol
            signal: Signal type ('BUY_AT_LOW', 'SELL_AT_HIGH', 'HOLD')
        """
        symbol_state = self.get_symbol_state(symbol)
        symbol_state['last_signal'] = {
            'signal': signal,
            'timestamp': datetime.now().isoformat()
        }
        self._save_state()

    def get_all_symbols(self) -> List[str]:
        """Get list of all tracked symbols"""
        return list(self.state['symbols'].keys())

    def print_state_summary(self):
        """Print summary of current state"""
        print(f"\n{'='*70}")
        print("TRADING SYSTEM STATE SUMMARY")
        print(f"{'='*70}")
        print(f"Last Updated: {self.state.get('last_updated', 'Never')}")
        print(f"Tracked Symbols: {len(self.state['symbols'])}")

        for symbol, data in self.state['symbols'].items():
            print(f"\n{symbol}:")
            print(f"  Last Updated: {data.get('last_updated', 'Never')}")

            metrics = data.get('metrics', {})
            if metrics:
                print(f"  Current Price: ${metrics.get('current_price', 0):,.2f}")
                print(f"  30D High: ${metrics.get('30d_high', 0):,.2f}")
                print(f"  30D Low: ${metrics.get('30d_low', 0):,.2f}")

            orders = data.get('orders', {})
            active_buy = orders.get('active_buy')
            active_sell = orders.get('active_sell')

            if active_buy:
                print(f"  Active Buy: {active_buy['status']} - "
                      f"{active_buy['details'].get('quantity', 0)} shares @ "
                      f"${active_buy['details'].get('price', 0):.2f}")

            if active_sell:
                print(f"  Active Sell: {active_sell['status']} - "
                      f"{active_sell['details'].get('quantity', 0)} shares @ "
                      f"${active_sell['details'].get('price', 0):.2f}")

            last_signal = data.get('last_signal')
            if last_signal:
                print(f"  Last Signal: {last_signal['signal']} at {last_signal['timestamp']}")

        print(f"{'='*70}\n")


def test_state_manager():
    """Test state manager"""
    import tempfile

    # Use temporary file for testing
    with tempfile.NamedTemporaryFile(mode='w', delete=False, suffix='.json') as f:
        temp_file = f.name

    try:
        print("Testing State Manager")
        print("=" * 70)

        # Initialize
        manager = StateManager(temp_file)

        # Update metrics
        print("\n1. Updating metrics for BTC:")
        manager.update_metrics('BTC', {
            'current_price': 42000.00,
            'intraday_high': 43000.00,
            'intraday_low': 41000.00,
            '30d_high': 45000.00,
            '30d_low': 38000.00
        })

        # Queue orders
        print("2. Queuing buy order for BTC:")
        manager.queue_buy_order('BTC', {
            'quantity': 0.1,
            'price': 38000.00,
            'trigger': '30d_low'
        })

        print("3. Queuing sell order for BTC:")
        manager.queue_sell_order('BTC', {
            'quantity': 0.1,
            'price': 45000.00,
            'trigger': '30d_high'
        })

        # Set signal
        manager.set_last_signal('BTC', 'HOLD')

        # Print summary
        manager.print_state_summary()

        # Test order status update
        print("4. Updating order status:")
        manager.update_order_status('BTC', 'buy', 'placed', order_id='ORDER123')
        manager.print_state_summary()

    finally:
        # Clean up
        if os.path.exists(temp_file):
            os.remove(temp_file)


if __name__ == "__main__":
    test_state_manager()
