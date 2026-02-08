from trading_system.entities.Order import Order
from trading_system.entities.OrderType import OrderType
from trading_system.entities.Ticker import Ticker

def make_orders():
    return [
        Order(100, 50.0, OrderType.MARKET),
        Order(200, 75.0, OrderType.LIMIT),
        Order(150, 60.0, OrderType.STOP),
    ]

class TestGetOpenOrders:
    def test_returns_all(self):
        orders = make_orders()
        ticker = Ticker(orders)
        assert ticker.get_open_orders() == orders

    def test_empty(self):
        ticker = Ticker([])
        assert ticker.get_open_orders() == []

class TestGetValidOrders:
    def test_initially_all_valid(self):
        orders = make_orders()
        ticker = Ticker(orders)
        assert len(ticker.get_valid_orders()) == 3
        assert ticker.get_valid_orders() == orders

    def test_filters_invalid(self):
        orders = make_orders()
        ticker = Ticker(orders)
        orders[1].mark_invalid()
        valid = ticker.get_valid_orders()
        assert len(valid) == 2
        assert orders[1] not in valid
        assert orders[0] in valid
        assert orders[2] in valid

class TestOrderValidity:
    def test_orders_start_valid(self):
        orders = make_orders()
        ticker = Ticker(orders)
        assert all(order.is_valid for order in orders)

    def test_mark_invalid(self):
        orders = make_orders()
        ticker = Ticker(orders)
        orders[0].mark_invalid()
        assert orders[0].is_valid is False
        assert len(ticker.get_valid_orders()) == 2