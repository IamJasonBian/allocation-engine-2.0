from trading_system.entities.OrderType import OrderType

class TestOrderType:
    def test_all_types_exist(self):
        assert OrderType.MARKET.value == "market"
        assert OrderType.LIMIT.value == "limit"
        assert OrderType.STOP.value == "stop"
        assert OrderType.STOP_LIMIT.value == "stop_limit"
        assert OrderType.TRAILING_STOP.value == "trailing_stop"

    def test_count(self):
        assert len(OrderType) == 5