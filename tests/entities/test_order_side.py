from trading_system.entities.OrderType import OrderSide

class TestOrderSide:
    def test_sides_exist(self):
        assert OrderSide.BUY.value == "buy"
        assert OrderSide.SELL.value == "sell"

    def test_count(self):
        assert len(OrderSide) == 2