"""
Tests for MomentumDcaStrategy using Ticker/Order entities
"""
from trading_system.strategies.momentum_dca_strategy import MomentumDcaLongStrategy
from trading_system.entities.Order import Order
from trading_system.entities.OrderType import OrderType
from trading_system.entities.Ticker import Ticker
from trading_system.state.state_manager import StateManager


def _make_strategy(**kwargs):
    defaults = dict(symbols=['BTC', 'SPY'], coverage_threshold=0.20,
                    stop_offset_pct=0.015, proximity_pct=0.0075)
    defaults.update(kwargs)
    return MomentumDcaLongStrategy(**defaults)


def _make_position(symbol, quantity, price):
    return {'symbol': symbol, 'quantity': quantity, 'current_price': price,
            'equity': quantity * price}


def _make_ticker(orders=None):
    return Ticker(orders or [])


def _sell_order(size, price, order_type=OrderType.LIMIT):
    return Order(size=size, price=price, order_type=order_type)


class TestCoverageCalculation:
    def test_fully_covered(self):
        strategy = _make_strategy()
        position = _make_position('SPY', 100, 450.0)
        ticker = _make_ticker([_sell_order(25, 460.0)])
        signal = strategy.analyze_symbol('SPY', {'current_price': 450.0}, position, ticker)
        assert signal['signal'] == 'COVERED'
        assert signal['order'] is None

    def test_exactly_at_threshold(self):
        strategy = _make_strategy()
        position = _make_position('SPY', 100, 450.0)
        ticker = _make_ticker([_sell_order(20, 460.0)])
        signal = strategy.analyze_symbol('SPY', {'current_price': 450.0}, position, ticker)
        assert signal['signal'] == 'COVERED'

    def test_under_covered_no_existing_orders(self):
        strategy = _make_strategy()
        position = _make_position('SPY', 100, 450.0)
        ticker = _make_ticker()
        signal = strategy.analyze_symbol('SPY', {'current_price': 450.0}, position, ticker)
        assert signal['signal'] == 'COVER_GAP'
        assert signal['order']['action'] == 'stop_limit_sell'
        assert signal['order']['quantity'] == 20

    def test_partial_coverage(self):
        strategy = _make_strategy()
        position = _make_position('SPY', 100, 450.0)
        ticker = _make_ticker([_sell_order(10, 460.0)])
        signal = strategy.analyze_symbol('SPY', {'current_price': 450.0}, position, ticker)
        assert signal['order'] is not None
        assert signal['order']['quantity'] == 10

    def test_multiple_orders_count(self):
        strategy = _make_strategy()
        position = _make_position('SPY', 100, 450.0)
        ticker = _make_ticker([
            _sell_order(5, 460.0, OrderType.LIMIT),
            _sell_order(5, 440.0, OrderType.STOP),
            _sell_order(5, 438.0, OrderType.STOP_LIMIT),
            _sell_order(5, 435.0, OrderType.LIMIT),
        ])
        signal = strategy.analyze_symbol('SPY', {'current_price': 450.0}, position, ticker)
        assert signal['signal'] == 'COVERED'

    def test_out_of_range_orders_not_counted(self):
        """Orders >8% from current price don't count toward coverage"""
        strategy = _make_strategy()
        position = _make_position('SPY', 100, 450.0)
        # 25 shares @ $600 is 33% away — out of range
        ticker = _make_ticker([_sell_order(25, 600.0)])
        signal = strategy.analyze_symbol('SPY', {'current_price': 450.0}, position, ticker)
        assert signal['signal'] == 'COVER_GAP'
        assert signal['order']['quantity'] == 20

    def test_mixed_in_range_and_out_of_range(self):
        """Only in-range orders count; out-of-range are ignored for coverage"""
        strategy = _make_strategy()
        position = _make_position('SPY', 100, 450.0)
        ticker = _make_ticker([
            _sell_order(10, 460.0),   # 2.2% away — in range
            _sell_order(15, 600.0),   # 33% away — out of range
        ])
        signal = strategy.analyze_symbol('SPY', {'current_price': 450.0}, position, ticker)
        # Only 10 shares in range, need 20 for 20% of 100
        assert signal['order'] is not None
        assert signal['order']['quantity'] == 10

    def test_out_of_range_listed_in_covered_signal(self):
        """When covered, out-of-range orders appear in signal data"""
        strategy = _make_strategy()
        position = _make_position('SPY', 100, 450.0)
        ticker = _make_ticker([
            _sell_order(25, 460.0),   # 2.2% — in range, enough to cover
            _sell_order(10, 600.0),   # 33% — out of range
        ])
        signal = strategy.analyze_symbol('SPY', {'current_price': 450.0}, position, ticker)
        assert signal['signal'] == 'COVERED'
        assert len(signal['existing_orders']) == 1
        assert len(signal['out_of_range_orders']) == 1

    def test_invalid_orders_not_counted(self):
        strategy = _make_strategy()
        position = _make_position('SPY', 100, 450.0)
        order = _sell_order(25, 460.0)
        order.mark_invalid()
        ticker = _make_ticker([order])
        signal = strategy.analyze_symbol('SPY', {'current_price': 450.0}, position, ticker)
        assert signal['signal'] == 'COVER_GAP'
        assert signal['order']['quantity'] == 20


class TestGapQuantity:
    def test_gap_from_zero(self):
        strategy = _make_strategy()
        position = _make_position('SPY', 200, 450.0)
        signal = strategy.analyze_symbol('SPY', {'current_price': 450.0}, position, _make_ticker())
        assert signal['order']['quantity'] == 40

    def test_gap_from_partial(self):
        strategy = _make_strategy()
        position = _make_position('SPY', 200, 450.0)
        ticker = _make_ticker([_sell_order(15, 460.0)])
        signal = strategy.analyze_symbol('SPY', {'current_price': 450.0}, position, ticker)
        assert signal['order']['quantity'] == 25

    def test_btc_whole_shares(self):
        """BTC is Grayscale Bitcoin Mini Trust ETF — whole shares only"""
        strategy = _make_strategy()
        position = _make_position('BTC', 3262, 31.0)
        signal = strategy.analyze_symbol('BTC', {'current_price': 31.0}, position, _make_ticker())
        assert signal['order']['quantity'] == 300  # capped to lot_size
        assert isinstance(signal['order']['quantity'], int)

    def test_stock_whole_shares(self):
        strategy = _make_strategy()
        position = _make_position('SPY', 7, 450.0)
        signal = strategy.analyze_symbol('SPY', {'current_price': 450.0}, position, _make_ticker())
        assert signal['order']['quantity'] == 1
        assert isinstance(signal['order']['quantity'], int)


class TestStopLimitPricing:
    def test_stop_price_at_negative_1_5_pct(self):
        strategy = _make_strategy()
        position = _make_position('SPY', 100, 450.0)
        signal = strategy.analyze_symbol('SPY', {'current_price': 450.0}, position, _make_ticker())
        expected = round(450.0 * 0.985, 2)
        assert signal['order']['stop_price'] == expected
        assert signal['order']['limit_price'] == expected

    def test_stop_equals_limit(self):
        strategy = _make_strategy()
        position = _make_position('BTC', 100, 31.0)
        signal = strategy.analyze_symbol('BTC', {'current_price': 31.0}, position, _make_ticker())
        assert signal['order']['stop_price'] == signal['order']['limit_price']


class TestPriceProximity:
    def test_within_proximity_resubmits(self):
        strategy = _make_strategy()
        position = _make_position('SPY', 100, 450.0)
        ticker = _make_ticker([_sell_order(10, 452.0)])
        signal = strategy.analyze_symbol('SPY', {'current_price': 450.0}, position, ticker)
        assert signal['signal'] == 'RESUBMIT'
        assert signal['order']['action'] == 'limit_sell'
        assert signal['order']['price'] == 452.0

    def test_outside_proximity_new_stop_limit(self):
        strategy = _make_strategy()
        position = _make_position('SPY', 100, 450.0)
        ticker = _make_ticker([_sell_order(10, 500.0)])
        signal = strategy.analyze_symbol('SPY', {'current_price': 450.0}, position, ticker)
        assert signal['signal'] == 'COVER_GAP'
        assert signal['order']['action'] == 'stop_limit_sell'

    def test_resubmit_uses_order_entity_price(self):
        strategy = _make_strategy()
        position = _make_position('SPY', 100, 448.0)
        ticker = _make_ticker([_sell_order(10, 450.0)])
        signal = strategy.analyze_symbol('SPY', {'current_price': 448.0}, position, ticker)
        assert signal['signal'] == 'RESUBMIT'
        assert signal['order']['price'] == 450.0
        assert signal['order']['current_price'] == 448.0

    def test_proximity_with_stop_order(self):
        strategy = _make_strategy()
        position = _make_position('SPY', 100, 450.0)
        ticker = _make_ticker([_sell_order(10, 449.0, OrderType.STOP)])
        signal = strategy.analyze_symbol('SPY', {'current_price': 450.0}, position, ticker)
        assert signal['signal'] == 'RESUBMIT'

    def test_nearest_order_selected(self):
        strategy = _make_strategy()
        position = _make_position('SPY', 100, 450.0)
        ticker = _make_ticker([
            _sell_order(5, 500.0),
            _sell_order(5, 451.0),
        ])
        signal = strategy.analyze_symbol('SPY', {'current_price': 450.0}, position, ticker)
        assert signal['signal'] == 'RESUBMIT'
        assert signal['order']['price'] == 451.0


class TestEdgeCases:
    def test_no_position(self):
        strategy = _make_strategy()
        signal = strategy.analyze_symbol('SPY', {'current_price': 450.0}, None, _make_ticker())
        assert signal['signal'] == 'NO_POSITION'

    def test_zero_quantity_position(self):
        strategy = _make_strategy()
        position = _make_position('SPY', 0, 450.0)
        signal = strategy.analyze_symbol('SPY', {'current_price': 450.0}, position, _make_ticker())
        assert signal['signal'] == 'NO_POSITION'

    def test_no_price_data(self):
        strategy = _make_strategy()
        position = _make_position('SPY', 100, 450.0)
        signal = strategy.analyze_symbol('SPY', {}, position, _make_ticker())
        assert signal['signal'] == 'NO_DATA'

    def test_format_signal_cover_gap(self):
        strategy = _make_strategy()
        signal = {
            'signal': 'COVER_GAP', 'reason': 'test',
            'order': {'action': 'stop_limit_sell', 'symbol': 'SPY', 'quantity': 20,
                      'stop_price': 443.25, 'limit_price': 443.25, 'current_price': 450.0}
        }
        output = strategy.format_signal('SPY', signal)
        assert 'COVER_GAP' in output
        assert 'Stop Price' in output

    def test_format_signal_resubmit(self):
        strategy = _make_strategy()
        signal = {
            'signal': 'RESUBMIT', 'reason': 'test',
            'order': {'action': 'limit_sell', 'symbol': 'SPY', 'quantity': 10,
                      'price': 452.0, 'current_price': 450.0}
        }
        output = strategy.format_signal('SPY', signal)
        assert 'RESUBMIT' in output
        assert 'Limit Price' in output

    def test_format_signal_covered(self):
        strategy = _make_strategy()
        signal = {'signal': 'COVERED', 'reason': 'fully covered', 'order': None}
        output = strategy.format_signal('SPY', signal)
        assert 'COVERED' in output


class TestPairedBuyOrder:
    """COVER_GAP should include a paired limit buy 0.2% below stop price"""

    def test_cover_gap_includes_paired_buy(self):
        strategy = _make_strategy()
        position = _make_position('SPY', 100, 450.0)
        signal = strategy.analyze_symbol('SPY', {'current_price': 450.0}, position, _make_ticker())
        assert signal['signal'] == 'COVER_GAP'
        assert signal['paired_buy'] is not None
        assert signal['paired_buy']['action'] == 'limit_buy'

    def test_paired_buy_price_below_stop(self):
        strategy = _make_strategy()
        position = _make_position('SPY', 100, 450.0)
        signal = strategy.analyze_symbol('SPY', {'current_price': 450.0}, position, _make_ticker())
        stop_price = signal['order']['stop_price']
        buy_price = signal['paired_buy']['price']
        assert buy_price < stop_price
        expected = round(stop_price - 0.20, 2)
        assert buy_price == expected

    def test_paired_buy_same_quantity(self):
        strategy = _make_strategy()
        position = _make_position('SPY', 100, 450.0)
        signal = strategy.analyze_symbol('SPY', {'current_price': 450.0}, position, _make_ticker())
        assert signal['paired_buy']['quantity'] == signal['order']['quantity']

    def test_paired_buy_same_symbol(self):
        strategy = _make_strategy()
        position = _make_position('SPY', 100, 450.0)
        signal = strategy.analyze_symbol('SPY', {'current_price': 450.0}, position, _make_ticker())
        assert signal['paired_buy']['symbol'] == signal['order']['symbol']

    def test_resubmit_no_paired_buy(self):
        """RESUBMIT signals should not have a paired buy"""
        strategy = _make_strategy()
        position = _make_position('SPY', 100, 450.0)
        ticker = _make_ticker([_sell_order(10, 452.0)])
        signal = strategy.analyze_symbol('SPY', {'current_price': 450.0}, position, ticker)
        assert signal['signal'] == 'RESUBMIT'
        assert signal.get('paired_buy') is None

    def test_format_shows_paired_buy(self):
        strategy = _make_strategy()
        signal = {
            'signal': 'COVER_GAP', 'reason': 'test',
            'order': {'action': 'stop_limit_sell', 'symbol': 'SPY', 'quantity': 20,
                      'stop_price': 443.25, 'limit_price': 443.25, 'current_price': 450.0},
            'paired_buy': {'action': 'limit_buy', 'symbol': 'SPY', 'quantity': 20,
                           'price': 443.05, 'current_price': 450.0},
        }
        output = strategy.format_signal('SPY', signal)
        assert 'Paired Buy' in output
        assert '$443.05' in output


class TestHedgeSymbolMap:
    """Orders target the same symbol by default; custom map can override"""

    def test_btc_orders_stay_btc(self):
        """BTC is Grayscale Bitcoin Mini Trust ETF — no remapping"""
        strategy = _make_strategy()
        position = _make_position('BTC', 100, 31.0)
        signal = strategy.analyze_symbol('BTC', {'current_price': 31.0}, position, _make_ticker())
        assert signal['order']['symbol'] == 'BTC'

    def test_spy_orders_stay_spy(self):
        strategy = _make_strategy()
        position = _make_position('SPY', 100, 450.0)
        signal = strategy.analyze_symbol('SPY', {'current_price': 450.0}, position, _make_ticker())
        assert signal['order']['symbol'] == 'SPY'

    def test_custom_hedge_map(self):
        strategy = _make_strategy(hedge_symbol_map={'SPY': 'SH'})
        position = _make_position('SPY', 100, 450.0)
        signal = strategy.analyze_symbol('SPY', {'current_price': 450.0}, position, _make_ticker())
        assert signal['order']['symbol'] == 'SH'

    def test_format_shows_hedge_note_when_mapped(self):
        strategy = _make_strategy(hedge_symbol_map={'SPY': 'SH'})
        signal = {
            'signal': 'COVER_GAP', 'reason': 'test',
            'order': {'action': 'stop_limit_sell', 'symbol': 'SH', 'quantity': 20,
                      'stop_price': 443.25, 'limit_price': 443.25, 'current_price': 450.0}
        }
        output = strategy.format_signal('SPY', signal)
        assert 'SH' in output
        assert 'hedging SPY via SH' in output


class TestLoadBrokerOrders:
    """Test StateManager.load_broker_orders converts raw dicts to Ticker"""

    def test_loads_all_order_sides(self):
        mgr = StateManager()
        broker_orders = [
            {'symbol': 'SPY', 'side': 'BUY', 'order_type': 'Limit',
             'quantity': 50, 'limit_price': 440.0, 'stop_price': None},
            {'symbol': 'SPY', 'side': 'SELL', 'order_type': 'Limit',
             'quantity': 20, 'limit_price': 460.0, 'stop_price': None},
        ]
        mgr.load_broker_orders('SPY', broker_orders)
        ticker = mgr.get_ticker('SPY')
        assert len(ticker.orders) == 2

    def test_filters_by_symbol(self):
        mgr = StateManager()
        broker_orders = [
            {'symbol': 'AAPL', 'side': 'SELL', 'order_type': 'Limit',
             'quantity': 30, 'limit_price': 180.0, 'stop_price': None},
            {'symbol': 'SPY', 'side': 'SELL', 'order_type': 'Limit',
             'quantity': 20, 'limit_price': 460.0, 'stop_price': None},
        ]
        mgr.load_broker_orders('SPY', broker_orders)
        ticker = mgr.get_ticker('SPY')
        assert len(ticker.orders) == 1
        assert ticker.orders[0].price == 460.0

    def test_maps_order_types(self):
        mgr = StateManager()
        broker_orders = [
            {'symbol': 'SPY', 'side': 'SELL', 'order_type': 'Limit',
             'quantity': 5, 'limit_price': 460.0, 'stop_price': None},
            {'symbol': 'SPY', 'side': 'SELL', 'order_type': 'Stop Limit',
             'quantity': 5, 'limit_price': 438.0, 'stop_price': 440.0},
            {'symbol': 'SPY', 'side': 'SELL', 'order_type': 'Stop Loss',
             'quantity': 5, 'limit_price': None, 'stop_price': 440.0},
        ]
        mgr.load_broker_orders('SPY', broker_orders)
        ticker = mgr.get_ticker('SPY')
        assert len(ticker.orders) == 3
        assert ticker.orders[0].order_type == OrderType.LIMIT
        assert ticker.orders[1].order_type == OrderType.STOP_LIMIT
        assert ticker.orders[2].order_type == OrderType.STOP

    def test_uses_limit_price_over_stop_price(self):
        mgr = StateManager()
        broker_orders = [
            {'symbol': 'SPY', 'side': 'SELL', 'order_type': 'Stop Limit',
             'quantity': 10, 'limit_price': 438.0, 'stop_price': 440.0},
        ]
        mgr.load_broker_orders('SPY', broker_orders)
        ticker = mgr.get_ticker('SPY')
        assert ticker.orders[0].price == 438.0

    def test_falls_back_to_stop_price(self):
        mgr = StateManager()
        broker_orders = [
            {'symbol': 'SPY', 'side': 'SELL', 'order_type': 'Stop Loss',
             'quantity': 10, 'limit_price': None, 'stop_price': 440.0},
        ]
        mgr.load_broker_orders('SPY', broker_orders)
        ticker = mgr.get_ticker('SPY')
        assert ticker.orders[0].price == 440.0

    def test_replaces_existing_ticker(self):
        mgr = StateManager()
        mgr.load_broker_orders('SPY', [
            {'symbol': 'SPY', 'side': 'SELL', 'order_type': 'Limit',
             'quantity': 10, 'limit_price': 450.0, 'stop_price': None},
        ])
        assert len(mgr.get_ticker('SPY').orders) == 1

        mgr.load_broker_orders('SPY', [
            {'symbol': 'SPY', 'side': 'SELL', 'order_type': 'Limit',
             'quantity': 5, 'limit_price': 460.0, 'stop_price': None},
            {'symbol': 'SPY', 'side': 'SELL', 'order_type': 'Limit',
             'quantity': 5, 'limit_price': 470.0, 'stop_price': None},
        ])
        assert len(mgr.get_ticker('SPY').orders) == 2
