"""
State Management Module
Stores and manages metrics and order state for each symbol.
Uses entity classes (Order, Ticker, OrderType) for order management.
"""

import json
import os
from datetime import datetime
from typing import Dict, List, Optional

from trading_system.entities.Order import Order
from trading_system.entities.OrderType import OrderType, OrderSide
from trading_system.entities.Ticker import Ticker


class StateManager:
    """Manages persistent state for trading system"""

    def __init__(self, state_file: str = 'trading_state.json'):
        self.state_file = state_file
        self.state = self._load_state()
        self.tickers: Dict[str, Ticker] = {}
        self._init_tickers()

    def _init_tickers(self):
        """Initialize a Ticker for each symbol already in persisted state"""
        for symbol in self.state.get('symbols', {}):
            orders = self._load_orders_for_symbol(symbol)
            self.tickers[symbol] = Ticker(orders)

    def _load_orders_for_symbol(self, symbol: str) -> List[Order]:
        """Reconstruct Order objects from persisted symbol state"""
        symbol_data = self.state['symbols'].get(symbol, {})
        orders_data = symbol_data.get('orders', {})
        orders = []

        for key in ['active_buy', 'active_sell']:
            raw = orders_data.get(key)
            if raw and raw.get('details'):
                details = raw['details']
                order_type_str = details.get('order_type', 'market')
                try:
                    order_type = OrderType(order_type_str)
                except ValueError:
                    order_type = OrderType.MARKET

                order = Order(
                    size=details.get('quantity', 0),
                    price=details.get('price', 0),
                    order_type=order_type,
                )
                # Mark cancelled/filled orders as invalid
                if raw.get('status') in ['filled', 'cancelled']:
                    order.mark_invalid()

                orders.append(order)

        return orders

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
            self.state['last_updated'] = datetime.now().isoformat()
            with open(self.state_file, 'w') as f:
                json.dump(self.state, f, indent=2)
        except Exception as e:
            print(f"Error saving state: {e}")

    def get_symbol_state(self, symbol: str) -> Dict:
        """Get state for a specific symbol, initializing a new Ticker if needed"""
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

        if symbol not in self.tickers:
            self.tickers[symbol] = Ticker([])

        return self.state['symbols'][symbol]

    def get_ticker(self, symbol: str) -> Ticker:
        """Get the Ticker for a symbol, initializing a new one if needed"""
        if symbol not in self.tickers:
            self.get_symbol_state(symbol)
        return self.tickers[symbol]

    def update_metrics(self, symbol: str, metrics: Dict):
        """Update metrics for a symbol"""
        symbol_state = self.get_symbol_state(symbol)
        symbol_state['metrics'] = metrics
        symbol_state['last_updated'] = datetime.now().isoformat()
        self._save_state()

    def get_metrics(self, symbol: str) -> Dict:
        """Get current metrics for a symbol"""
        return self.get_symbol_state(symbol).get('metrics', {})

    def queue_buy_order(self, symbol: str, order_details: Dict):
        """Queue a buy order using Order and Ticker entities"""
        symbol_state = self.get_symbol_state(symbol)
        ticker = self.get_ticker(symbol)

        order_type_str = order_details.get('order_type', 'market')
        try:
            order_type = OrderType(order_type_str)
        except ValueError:
            order_type = OrderType.MARKET

        order = Order(
            size=order_details.get('quantity', 0),
            price=order_details.get('price', 0),
            order_type=order_type,
        )
        ticker.orders.append(order)

        order_record = {
            'type': OrderSide.BUY.value,
            'status': 'queued',
            'queued_at': datetime.now().isoformat(),
            'details': order_details
        }

        symbol_state['orders']['active_buy'] = order_record
        symbol_state['orders']['order_history'].append(order_record.copy())
        self._save_state()

    def queue_sell_order(self, symbol: str, order_details: Dict):
        """Queue a sell order using Order and Ticker entities"""
        symbol_state = self.get_symbol_state(symbol)
        ticker = self.get_ticker(symbol)

        order_type_str = order_details.get('order_type', 'market')
        try:
            order_type = OrderType(order_type_str)
        except ValueError:
            order_type = OrderType.MARKET

        order = Order(
            size=order_details.get('quantity', 0),
            price=order_details.get('price', 0),
            order_type=order_type,
        )
        ticker.orders.append(order)

        order_record = {
            'type': OrderSide.SELL.value,
            'status': 'queued',
            'queued_at': datetime.now().isoformat(),
            'details': order_details
        }

        symbol_state['orders']['active_sell'] = order_record
        symbol_state['orders']['order_history'].append(order_record.copy())
        self._save_state()

    def update_order_status(self, symbol: str, order_type: str, status: str,
                            order_id: Optional[str] = None):
        """Update order status, marking the entity Order invalid when filled/cancelled"""
        symbol_state = self.get_symbol_state(symbol)
        ticker = self.get_ticker(symbol)

        order_key = f'active_{order_type}'
        if symbol_state['orders'][order_key]:
            symbol_state['orders'][order_key]['status'] = status
            symbol_state['orders'][order_key]['last_updated'] = datetime.now().isoformat()

            if order_id:
                symbol_state['orders'][order_key]['order_id'] = order_id

            if status in ['filled', 'cancelled']:
                symbol_state['orders'][order_key] = None
                # Mark matching orders invalid on the Ticker
                for order in ticker.orders:
                    if not order.is_valid:
                        continue
                    details = symbol_state['orders'].get(order_key)
                    order.mark_invalid()
                    break

            self._save_state()

    def get_active_orders(self, symbol: str) -> Dict:
        """Get active orders for a symbol, using the Ticker for valid order tracking"""
        symbol_state = self.get_symbol_state(symbol)
        ticker = self.get_ticker(symbol)

        return {
            'buy': symbol_state['orders']['active_buy'],
            'sell': symbol_state['orders']['active_sell'],
            'valid_orders': [o.get_state() for o in ticker.get_valid_orders()]
        }

    def get_order_history(self, symbol: str, limit: int = 10) -> List[Dict]:
        """Get order history for a symbol"""
        symbol_state = self.get_symbol_state(symbol)
        history = symbol_state['orders']['order_history']
        return history[-limit:] if history else []

    def set_last_signal(self, symbol: str, signal: str):
        """Record last trading signal"""
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
        """Print summary of current state, including Ticker info"""
        print(f"\n{'='*70}")
        print("TRADING SYSTEM STATE SUMMARY")
        print(f"{'='*70}")
        print(f"Last Updated: {self.state.get('last_updated', 'Never')}")
        print(f"Tracked Symbols: {len(self.state['symbols'])}")

        for symbol, data in self.state['symbols'].items():
            ticker = self.get_ticker(symbol)
            print(f"\n{symbol}:")
            print(f"  Last Updated: {data.get('last_updated', 'Never')}")
            print(f"  Ticker Orders: {len(ticker.orders)} total, "
                  f"{len(ticker.get_valid_orders())} valid")

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
    """Test state manager with entity classes"""
    import tempfile

    with tempfile.NamedTemporaryFile(mode='w', delete=False, suffix='.json') as f:
        temp_file = f.name

    try:
        print("Testing State Manager")
        print("=" * 70)

        manager = StateManager(temp_file)

        # Verify Ticker is initialized for new symbol
        print("\n1. Updating metrics for BTC:")
        manager.update_metrics('BTC', {
            'current_price': 42000.00,
            'intraday_high': 43000.00,
            'intraday_low': 41000.00,
            '30d_high': 45000.00,
            '30d_low': 38000.00
        })

        ticker = manager.get_ticker('BTC')
        print(f"   BTC Ticker initialized: {ticker is not None}")
        print(f"   BTC Ticker orders: {len(ticker.orders)}")

        # Queue buy order - creates Order entity on the Ticker
        print("2. Queuing buy order for BTC:")
        manager.queue_buy_order('BTC', {
            'quantity': 0.1,
            'price': 38000.00,
            'trigger': '30d_low',
            'order_type': 'market'
        })
        print(f"   Ticker orders after buy: {len(ticker.orders)}")
        print(f"   Order type: {ticker.orders[-1].order_type}")

        # Queue sell order - creates another Order entity
        print("3. Queuing sell order for BTC:")
        manager.queue_sell_order('BTC', {
            'quantity': 0.1,
            'price': 45000.00,
            'trigger': '30d_high',
            'order_type': 'limit'
        })
        print(f"   Ticker orders after sell: {len(ticker.orders)}")
        print(f"   Valid orders: {len(ticker.get_valid_orders())}")

        manager.set_last_signal('BTC', 'HOLD')
        manager.print_state_summary()

        # Update order status
        print("4. Updating order status:")
        manager.update_order_status('BTC', 'buy', 'placed', order_id='ORDER123')
        manager.print_state_summary()

        # Verify active orders include Ticker data
        active = manager.get_active_orders('BTC')
        print(f"5. Active orders valid_orders count: {len(active['valid_orders'])}")

    finally:
        if os.path.exists(temp_file):
            os.remove(temp_file)


if __name__ == "__main__":
    test_state_manager()
