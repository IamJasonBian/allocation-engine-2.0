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


class TestQueueOrderEntities:
    def test_buy_order_creates_order_entity(self):
        mgr = StateManager()
        mgr.queue_buy_order('BTC', {
            'quantity': 0.1,
            'price': 38000.00,
            'order_type': 'market'
        })
        ticker = mgr.get_ticker('BTC')
        assert len(ticker.orders) == 1
        assert isinstance(ticker.orders[0], Order)
        assert ticker.orders[0].order_type == OrderType.MARKET
        assert ticker.orders[0].price == 38000.00
        assert ticker.orders[0].size == 0.1

    def test_sell_order_creates_order_entity(self):
        mgr = StateManager()
        mgr.queue_sell_order('BTC', {
            'quantity': 5,
            'price': 45000.00,
            'order_type': 'limit'
        })
        ticker = mgr.get_ticker('BTC')
        assert len(ticker.orders) == 1
        assert ticker.orders[0].order_type == OrderType.LIMIT

    def test_multiple_orders_accumulate_on_ticker(self):
        mgr = StateManager()
        mgr.queue_buy_order('BTC', {'quantity': 1, 'price': 100, 'order_type': 'market'})
        mgr.queue_sell_order('BTC', {'quantity': 1, 'price': 200, 'order_type': 'limit'})
        ticker = mgr.get_ticker('BTC')
        assert len(ticker.orders) == 2
        assert len(ticker.get_valid_orders()) == 2

    def test_invalid_order_type_defaults_to_market(self):
        mgr = StateManager()
        mgr.queue_buy_order('BTC', {'quantity': 1, 'price': 100, 'order_type': 'unknown'})
        ticker = mgr.get_ticker('BTC')
        assert ticker.orders[0].order_type == OrderType.MARKET


class TestActiveOrdersIncludeTickerData:
    def test_valid_orders_in_response(self):
        mgr = StateManager()
        mgr.queue_buy_order('BTC', {'quantity': 1, 'price': 100, 'order_type': 'market'})
        active = mgr.get_active_orders('BTC')
        assert 'valid_orders' in active
        assert len(active['valid_orders']) == 1


class TestUpdateOrderStatus:
    def test_filled_order_marks_ticker_order_invalid(self):
        mgr = StateManager()
        mgr.queue_buy_order('BTC', {'quantity': 1, 'price': 100, 'order_type': 'market'})
        mgr.update_order_status('BTC', 'buy', 'filled', order_id='ORDER1')
        ticker = mgr.get_ticker('BTC')
        assert len(ticker.get_valid_orders()) == 0
        assert len(ticker.orders) == 1

    def test_cancelled_order_marks_ticker_order_invalid(self):
        mgr = StateManager()
        mgr.queue_sell_order('BTC', {'quantity': 1, 'price': 200, 'order_type': 'limit'})
        mgr.update_order_status('BTC', 'sell', 'cancelled')
        ticker = mgr.get_ticker('BTC')
        assert len(ticker.get_valid_orders()) == 0
