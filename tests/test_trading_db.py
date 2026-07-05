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


def test_post_orders_skips_when_empty():
    with mock.patch.object(tdb.requests, "post") as p:
        assert tdb.post_orders() is None
        assert tdb.post_orders(open_orders=[], recent_option_orders=[]) is None
        p.assert_not_called()


def test_post_bot_activity_shape():
    with mock.patch.object(tdb.requests, "post", return_value=_ok_response()) as p:
        tdb.post_bot_activity([{"order_id": "x", "type": "TRAILING_STOP_ORDER",
                                "status": "confirmed", "symbol": "AAPL"}])
        assert p.call_args.args[0].endswith("/db-bot-activity")
        assert "events" in p.call_args.kwargs["json"]


def test_failures_never_raise():
    with mock.patch.object(tdb.requests, "post",
                           side_effect=tdb.requests.RequestException("down")):
        assert tdb.post_orders(open_orders=[{"id": "1"}]) is None
        assert tdb.post_bot_activity([{"order_id": "x", "status": "s"}]) is None


def test_ok_false_treated_as_failure():
    bad = _ok_response({"ok": False, "error": {"message": "bad shape"}})
    with mock.patch.object(tdb.requests, "post", return_value=bad):
        assert tdb.post_orders(open_orders=[{"id": "1"}]) is None


def test_token_header_when_configured(monkeypatch):
    monkeypatch.setattr(tdb.Config, "TRADING_DB_TOKEN", "sekret")
    with mock.patch.object(tdb.requests, "post", return_value=_ok_response()) as p:
        tdb.post_orders(open_orders=[{"id": "1"}])
        assert p.call_args.kwargs["headers"]["Authorization"] == "Bearer sekret"


def test_no_token_header_by_default():
    with mock.patch.object(tdb.requests, "post", return_value=_ok_response()) as p:
        tdb.post_orders(open_orders=[{"id": "1"}])
        assert "Authorization" not in p.call_args.kwargs["headers"]
