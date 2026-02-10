"""
State Management Module
Stores and manages metrics and order state for each symbol.
Uses entity classes (Order, Ticker, OrderType) for order management.
State is held in-memory only (no file persistence).
"""

from datetime import datetime
from typing import Dict, List, Optional

from trading_system.entities.Order import Order
from trading_system.entities.OrderType import OrderType, OrderSide
from trading_system.entities.Ticker import Ticker


class StateManager:
    """Manages in-memory state for trading system"""

    def __init__(self):
        self.state: Dict = {
            'symbols': {},
            'last_updated': None
        }
        self.tickers: Dict[str, Ticker] = {}

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
        self.state['last_updated'] = datetime.now().isoformat()

    def get_metrics(self, symbol: str) -> Dict:
        """Get current metrics for a symbol"""
        return self.get_symbol_state(symbol).get('metrics', {})

    def queue_buy_order(self, symbol: str, order_details: Dict):
        """Queue a buy order, tracked in symbol state (not on Ticker)"""
        symbol_state = self.get_symbol_state(symbol)

        order_record = {
            'type': OrderSide.BUY.value,
            'status': 'queued',
            'queued_at': datetime.now().isoformat(),
            'details': order_details
        }

        symbol_state['orders']['active_buy'] = order_record
        symbol_state['orders']['order_history'].append(order_record.copy())
        self.state['last_updated'] = datetime.now().isoformat()

    def queue_sell_order(self, symbol: str, order_details: Dict):
        """Queue a sell order, tracked in symbol state (not on Ticker)"""
        symbol_state = self.get_symbol_state(symbol)

        order_record = {
            'type': OrderSide.SELL.value,
            'status': 'queued',
            'queued_at': datetime.now().isoformat(),
            'details': order_details
        }

        symbol_state['orders']['active_sell'] = order_record
        symbol_state['orders']['order_history'].append(order_record.copy())
        self.state['last_updated'] = datetime.now().isoformat()

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
                for order in ticker.orders:
                    if order.is_valid:
                        order.mark_invalid()
                        break

            self.state['last_updated'] = datetime.now().isoformat()

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
        self.state['last_updated'] = datetime.now().isoformat()

    def load_broker_sell_orders(self, symbol: str, broker_orders: List[Dict]):
        """Convert raw broker sell orders into Order entities on the symbol's Ticker.

        Filters to SELL orders for the given symbol, maps broker order_type
        strings to OrderType enums, and uses limit_price (or stop_price) as
        Order.price. Replaces any existing orders on the Ticker.
        """
        self.get_symbol_state(symbol)

        ORDER_TYPE_MAP = {
            'Limit': OrderType.LIMIT,
            'Market': OrderType.MARKET,
            'Stop Loss': OrderType.STOP,
            'Stop Limit': OrderType.STOP_LIMIT,
        }

        orders = []
        for raw in broker_orders:
            if raw.get('symbol') != symbol:
                continue
            if raw.get('side') != 'SELL':
                continue

            order_type = ORDER_TYPE_MAP.get(raw.get('order_type'), OrderType.MARKET)
            price = raw.get('limit_price') or raw.get('stop_price') or 0
            size = float(raw.get('quantity', 0))

            orders.append(Order(size=size, price=price, order_type=order_type))

        self.tickers[symbol] = Ticker(orders)

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
