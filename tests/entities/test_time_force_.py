from trading_system.entities.OrderType import TimeInForce

class TestTimeInForce:
    def test_types_exist(self):
        assert TimeInForce.GFD.value == "good_for_day"
        assert TimeInForce.GTC.value == "good_til_canceled"

    def test_count(self):
        assert len(TimeInForce) == 2