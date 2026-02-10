from trading_system.state.state_manager import StateManager
from trading_system.entities.Order import Order
from trading_system.entities.OrderType import OrderType
from trading_system.entities.Ticker import Ticker


class TestTickerInitialization:
    def test_new_symbol_gets_ticker(self):
        mgr = StateManager()
        mgr.get_symbol_state('BTC')
        assert 'BTC' in mgr.tickers
        assert isinstance(mgr.tickers['BTC'], Ticker)

    def test_ticker_starts_empty(self):
        mgr = StateManager()
        ticker = mgr.get_ticker('SPY')
        assert len(ticker.orders) == 0

    def test_each_symbol_gets_own_ticker(self):
        mgr = StateManager()
        t1 = mgr.get_ticker('BTC')
        t2 = mgr.get_ticker('SPY')
        assert t1 is not t2


class TestQueueOrderState:
    """Queued orders are tracked in symbol state, not on the Ticker."""

    def test_buy_order_stored_in_state(self):
        mgr = StateManager()
        mgr.queue_buy_order('BTC', {
            'quantity': 0.1,
            'price': 38000.00,
            'order_type': 'market'
        })
        state = mgr.get_symbol_state('BTC')
        assert state['orders']['active_buy'] is not None
        assert state['orders']['active_buy']['status'] == 'queued'
        assert state['orders']['active_buy']['details']['price'] == 38000.00

    def test_sell_order_stored_in_state(self):
        mgr = StateManager()
        mgr.queue_sell_order('BTC', {
            'quantity': 5,
            'price': 45000.00,
            'order_type': 'limit'
        })
        state = mgr.get_symbol_state('BTC')
        assert state['orders']['active_sell'] is not None
        assert state['orders']['active_sell']['status'] == 'queued'

    def test_queued_orders_do_not_appear_on_ticker(self):
        mgr = StateManager()
        mgr.queue_buy_order('BTC', {'quantity': 1, 'price': 100, 'order_type': 'market'})
        mgr.queue_sell_order('BTC', {'quantity': 1, 'price': 200, 'order_type': 'limit'})
        ticker = mgr.get_ticker('BTC')
        assert len(ticker.orders) == 0

    def test_order_history_appended(self):
        mgr = StateManager()
        mgr.queue_buy_order('BTC', {'quantity': 1, 'price': 100, 'order_type': 'market'})
        mgr.queue_sell_order('BTC', {'quantity': 1, 'price': 200, 'order_type': 'limit'})
        history = mgr.get_order_history('BTC')
        assert len(history) == 2


class TestTickerHoldsBrokerOrders:
    """Ticker only holds live broker orders loaded via load_broker_sell_orders."""

    def test_broker_orders_on_ticker(self):
        mgr = StateManager()
        broker_orders = [
            {'symbol': 'BTC', 'side': 'SELL', 'order_type': 'Limit',
             'quantity': '100', 'limit_price': 31.0},
            {'symbol': 'BTC', 'side': 'SELL', 'order_type': 'Stop Limit',
             'quantity': '50', 'limit_price': 29.0, 'stop_price': 29.5},
        ]
        mgr.load_broker_sell_orders('BTC', broker_orders)
        ticker = mgr.get_ticker('BTC')
        assert len(ticker.orders) == 2
        assert all(isinstance(o, Order) for o in ticker.orders)
        assert ticker.orders[0].order_type == OrderType.LIMIT
        assert ticker.orders[1].order_type == OrderType.STOP_LIMIT

    def test_valid_orders_in_active_response(self):
        mgr = StateManager()
        broker_orders = [
            {'symbol': 'BTC', 'side': 'SELL', 'order_type': 'Limit',
             'quantity': '100', 'limit_price': 31.0},
        ]
        mgr.load_broker_sell_orders('BTC', broker_orders)
        active = mgr.get_active_orders('BTC')
        assert 'valid_orders' in active
        assert len(active['valid_orders']) == 1


class TestUpdateOrderStatus:
    def test_filled_order_marks_ticker_order_invalid(self):
        mgr = StateManager()
        broker_orders = [
            {'symbol': 'BTC', 'side': 'SELL', 'order_type': 'Limit',
             'quantity': '100', 'limit_price': 31.0},
        ]
        mgr.load_broker_sell_orders('BTC', broker_orders)
        mgr.queue_sell_order('BTC', {'quantity': 100, 'price': 31.0, 'order_type': 'limit'})
        mgr.update_order_status('BTC', 'sell', 'filled', order_id='ORDER1')
        ticker = mgr.get_ticker('BTC')
        assert len(ticker.get_valid_orders()) == 0
        assert len(ticker.orders) == 1

    def test_cancelled_order_marks_ticker_order_invalid(self):
        mgr = StateManager()
        broker_orders = [
            {'symbol': 'BTC', 'side': 'SELL', 'order_type': 'Limit',
             'quantity': '100', 'limit_price': 31.0},
        ]
        mgr.load_broker_sell_orders('BTC', broker_orders)
        mgr.queue_sell_order('BTC', {'quantity': 100, 'price': 31.0, 'order_type': 'limit'})
        mgr.update_order_status('BTC', 'sell', 'cancelled')
        ticker = mgr.get_ticker('BTC')
        assert len(ticker.get_valid_orders()) == 0
