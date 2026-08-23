"""Offline tests for app/trading_db.py — Trading DB write path."""

from unittest import mock

from app import trading_db as tdb


def _ok_response(payload=None):
    r = mock.Mock()
    r.ok = True
    r.status_code = 200
    r.content = b"{}"
    r.json.return_value = payload or {"ok": True, "data": {}}
    return r


def test_post_orders_engine_dump_shape():
    with mock.patch.object(tdb.requests, "post", return_value=_ok_response()) as p:
        tdb.post_orders(open_orders=[{"id": "1"}],
                        recent_option_orders=[{"order_id": "2", "legs": []}])
        url = p.call_args.args[0]
        body = p.call_args.kwargs["json"]
        assert url.endswith("/db-orders")
        assert body == {"open_orders": [{"id": "1"}],
                        "recent_option_orders": [{"order_id": "2", "legs": []}]}


def test_post_positions_whole_book_shape():
    with mock.patch.object(tdb.requests, "post", return_value=_ok_response()) as p:
        tdb.post_positions(
            positions=[{"symbol": "AAPL", "qty": 3}],
            option_positions=[{"chain_symbol": "IWN", "strike": 20}],
            account={"equity": 100.0},
        )
        assert p.call_args.args[0].endswith("/db-positions")
        assert p.call_args.kwargs["json"] == {
            "positions": [{"symbol": "AAPL", "qty": 3}],
            "option_positions": [{"chain_symbol": "IWN", "strike": 20}],
            "account": {"equity": 100.0},
        }


def test_post_positions_sends_empty_book_to_clear_closed_names():
    # An empty book is meaningful: it tells the DB to drop every stale row.
    # It must post even with no account attached.
    with mock.patch.object(tdb.requests, "post", return_value=_ok_response()) as p:
        tdb.post_positions(positions=[], option_positions=[])
        assert p.call_args.args[0].endswith("/db-positions")
        assert p.call_args.kwargs["json"] == {"positions": [],
                                              "option_positions": []}


def test_failures_never_raise():
    with mock.patch.object(tdb.requests, "post",
                           side_effect=tdb.requests.RequestException("down")):
        assert tdb.post_orders(open_orders=[{"id": "1"}]) is None
        assert tdb.post_bot_activity([{"order_id": "x", "status": "s"}]) is None


def test_fills_from_orders_keeps_partial_cancel_drops_unfilled():
    payload = {"data": {
        "historical_orders": [
            {"symbol": "CRDO", "side": "BUY", "filled_quantity": 10,
             "average_price": 225, "updated_at": "2026-08-21T14:20:00Z"},
            {"symbol": "NBIS", "side": "BUY", "filled_quantity": 0,
             "average_price": None, "updated_at": "2026-08-21T20:00:00Z"},
        ],
        "open_orders": [],
        "untracked_orders": [],
    }}
    fills = tdb.fills_from_orders(payload)
    assert len(fills) == 1
    assert fills[0]["symbol"] == "CRDO"
    assert fills[0]["qty"] == 10
    assert fills[0]["price"] == 225


def test_token_header_when_configured(monkeypatch):
    monkeypatch.setattr(tdb.Config, "TRADING_DB_TOKEN", "sekret")
    with mock.patch.object(tdb.requests, "post", return_value=_ok_response()) as p:
        tdb.post_orders(open_orders=[{"id": "1"}])
        assert p.call_args.kwargs["headers"]["Authorization"] == "Bearer sekret"

