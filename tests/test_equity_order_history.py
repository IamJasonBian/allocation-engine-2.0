"""Filled equity orders must reach the Trading DB.

The sync only ever posted `open_orders`, and an order stops being open the
moment it fills — so `historical_orders` never grew from the engine. It sat
frozen at 2026-07-02 (a one-off backfill) while option orders, which are sent
in full, stayed current.
"""

from unittest import mock

from app.background import _equity_order_history


class _Broker:
    def __init__(self, orders=None, raises=False):
        self._orders = orders or []
        self._raises = raises
        self.limit = None

    def order_history(self, limit=50):
        self.limit = limit
        if self._raises:
            raise RuntimeError("robinhood 401")
        return self._orders


FILLED = {"id": "ord-1", "symbol": "NVDA", "side": "BUY", "quantity": 2.0,
          "filled_quantity": 2.0, "price": 110.5, "limit_price": 112.0,
          "type": "limit", "state": "filled",
          "created_at": "2026-08-04T14:00:00Z",
          "updated_at": "2026-08-04T14:00:02Z"}


def test_average_price_is_carried_across():
    # The DB reads `average_price`; order_history calls it `price`. Without
    # this copy, P&L silently falls back to the limit price.
    out = _equity_order_history(_Broker([dict(FILLED)]))
    assert out[0]["average_price"] == 110.5
    assert out[0]["limit_price"] == 112.0


def test_an_existing_average_price_is_not_overwritten():
    order = dict(FILLED, average_price=99.0)
    assert _equity_order_history(_Broker([order]))[0]["average_price"] == 99.0


def test_a_market_order_with_no_price_is_left_alone():
    order = {k: v for k, v in FILLED.items() if k != "price"}
    assert "average_price" not in _equity_order_history(_Broker([order]))[0]


def test_a_broker_failure_never_breaks_the_sync():
    assert _equity_order_history(_Broker(raises=True)) == []


def test_a_broker_without_history_is_tolerated():
    assert _equity_order_history(object()) == []


def test_limit_is_passed_through():
    broker = _Broker([])
    _equity_order_history(broker, limit=25)
    assert broker.limit == 25


def test_history_is_posted_as_recent_orders():
    from app import trading_db as tdb

    resp = mock.Mock(ok=True, status_code=200, content=b"{}")
    resp.json.return_value = {"ok": True, "data": {}}
    with mock.patch.object(tdb.requests, "post", return_value=resp) as p:
        tdb.post_orders(open_orders=[], recent_orders=[dict(FILLED)])
        body = p.call_args.kwargs["json"]

    # recent_orders is the key db-orders buckets into stock_orders
    assert body["recent_orders"][0]["id"] == "ord-1"
    assert "open_orders" not in body
