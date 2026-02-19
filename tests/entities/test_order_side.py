from trading_system.entities.OrderType import OrderSide, OrderType
from trading_system.entities.Order import Order


class TestOrderSide:
    def test_sides_exist(self):
        assert OrderSide.BUY.value == "buy"
        assert OrderSide.SELL.value == "sell"

    def test_count(self):
        assert len(OrderSide) == 2

    def test_order_with_side(self):
        order = Order(size=10, price=100, order_type=OrderType.LIMIT, side=OrderSide.BUY)
        assert order.side == OrderSide.BUY

    def test_order_side_defaults_none(self):
        order = Order(size=10, price=100, order_type=OrderType.LIMIT)
        assert order.side is None

    def test_order_side_in_state(self):
        order = Order(size=10, price=100, order_type=OrderType.LIMIT,
                      side=OrderSide.SELL, order_id='abc123', created_at='2025-01-01')
        state = order.get_state()
        assert state['side'] == OrderSide.SELL
        assert state['order_id'] == 'abc123'
        assert state['created_at'] == '2025-01-01'