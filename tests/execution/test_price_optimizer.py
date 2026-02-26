"""Tests for PriceOptimizer"""

from trading_system.execution.price_optimizer import PriceOptimizer


class TestOptimalLimitPrice:
    def test_buy_low_urgency_near_bid(self):
        optimizer = PriceOptimizer()
        price = optimizer.optimal_limit_price(
            'buy', bid=100.0, ask=100.20, urgency=0.0, time_of_day='midday')
        # Low urgency buy should be near bid
        assert price is not None
        assert price < 100.10  # Should be closer to bid than mid

    def test_buy_high_urgency_near_mid(self):
        optimizer = PriceOptimizer()
        price = optimizer.optimal_limit_price(
            'buy', bid=100.0, ask=100.20, urgency=1.0, time_of_day='midday')
        # High urgency buy should be near mid or above
        assert price is not None
        assert price >= 100.10  # Should be at or above mid

    def test_sell_low_urgency_near_ask(self):
        optimizer = PriceOptimizer()
        price = optimizer.optimal_limit_price(
            'sell', bid=100.0, ask=100.20, urgency=0.0, time_of_day='midday')
        assert price is not None
        assert price > 100.10  # Should be closer to ask

    def test_sell_high_urgency_near_mid(self):
        optimizer = PriceOptimizer()
        price = optimizer.optimal_limit_price(
            'sell', bid=100.0, ask=100.20, urgency=1.0, time_of_day='midday')
        assert price is not None
        assert price <= 100.10  # Should be at or below mid

    def test_open_more_conservative_than_midday(self):
        optimizer = PriceOptimizer()
        price_open = optimizer.optimal_limit_price(
            'buy', bid=100.0, ask=100.20, urgency=0.5, time_of_day='open')
        price_midday = optimizer.optimal_limit_price(
            'buy', bid=100.0, ask=100.20, urgency=0.5, time_of_day='midday')
        assert price_open is not None
        assert price_midday is not None
        # At open, buy price should be lower (more conservative for buyer)
        assert price_open <= price_midday

    def test_wide_spread_returns_none(self):
        optimizer = PriceOptimizer()
        # Spread > 2%
        price = optimizer.optimal_limit_price(
            'buy', bid=100.0, ask=103.0, urgency=0.5, time_of_day='midday')
        assert price is None

    def test_none_inputs_returns_none(self):
        optimizer = PriceOptimizer()
        price = optimizer.optimal_limit_price('buy', bid=None, ask=None)
        assert price is None

    def test_zero_bid_returns_none(self):
        optimizer = PriceOptimizer()
        price = optimizer.optimal_limit_price('buy', bid=0, ask=100.0)
        assert price is None


class TestGetTimeOfDay:
    def test_returns_valid_string(self):
        optimizer = PriceOptimizer()
        result = optimizer.get_time_of_day()
        assert result in ('open', 'early', 'midday', 'close')
