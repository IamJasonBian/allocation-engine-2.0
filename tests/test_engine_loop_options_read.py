"""A failed options read must skip option alerts and the whole-book Trading DB write."""

import pytest

from app.brokers.base import BrokerReadError


class _Stop(BaseException):
    pass


class _Broker:
    def __init__(self, options):
        self._options = options

    def positions(self): return [{"symbol": "NVDA", "qty": 1}]
    def open_orders(self): return []
    def account(self): return {"equity": 1.0, "cash": 1.0, "buying_power": 1.0,
                               "portfolio_value": 1.0}
    def options_orders(self, **_k): return []

    def options_positions(self):
        if isinstance(self._options, Exception):
            raise self._options
        return self._options


def _run_one_pass(monkeypatch, broker):
    from app import background
    calls = {"mental": 0, "pending": 0, "post_positions": 0, "post_orders": 0}

    def mental(pos, state, today=None, *, send):
        calls["mental"] += 1
        return [], state

    def pending(orders, sent, **_k):
        calls["pending"] += 1
        return [], sent

    def count(name, ret=None):
        def f(*_a, **_k):
            calls[name] += 1
            return ret
        return f

    noop = lambda *a, **k: None
    monkeypatch.setattr("app.brokers.get_broker", lambda _n: broker)
    monkeypatch.setattr("app.risk.mental_stops.check_mental_stops", mental)
    monkeypatch.setattr("app.risk.eod_summary.pending_summaries_due", pending)
    monkeypatch.setattr("app.trading_db.post_positions", count("post_positions"))
    monkeypatch.setattr("app.trading_db.post_orders", count("post_orders"))
    monkeypatch.setattr("app.redis_store.sync_to_redis", noop)
    monkeypatch.setattr("app.s3_store.sync_order_events", noop)
    monkeypatch.setattr("app.option_history_store.put_position_snapshot", noop)
    monkeypatch.setattr("app.option_history_store.put_order_snapshot", noop)
    monkeypatch.setattr("app.utils_shared.maybe_stop_sweep", noop)
    monkeypatch.setattr("app.utils_shared.maybe_option_take_profit_sweep", noop)

    def stop(_s):
        raise _Stop

    monkeypatch.setattr(background.time, "sleep", stop)
    app = type("A", (), {"config": {
        "POLL_INTERVAL_SECONDS": 1, "DRY_RUN": True, "TRADING_DB_SYNC_SECONDS": 0,
        "RH_RETRY_HOUR_ET": 11, "ENGINE_BROKER": "robinhood",
        "STOP_SWEEP_ENABLED": False, "STOP_SWEEP_DRY_RUN": True}})()
    with pytest.raises(_Stop):
        background.run_engine_loop(app)
    return calls


def test_failed_options_read_skips_alerts_summaries_and_position_write(monkeypatch, caplog):
    calls = _run_one_pass(monkeypatch, _Broker(BrokerReadError("None from RH")))
    assert calls["mental"] == 0 and calls["pending"] == 0 and calls["post_positions"] == 0
    assert calls["post_orders"] == 1
    assert "Failed to fetch options positions" in caplog.text


def test_successful_options_read_runs_checks_and_write(monkeypatch):
    calls = _run_one_pass(monkeypatch, _Broker([]))
    assert calls["mental"] == 1 and calls["pending"] == 1 and calls["post_positions"] == 1


def test_notify_reports_delivery(monkeypatch):
    from app import slack
    monkeypatch.setattr(slack, "_ALERTS_ENABLED", False)
    assert slack.notify("x") is False
    monkeypatch.setattr(slack, "_ALERTS_ENABLED", True)
    monkeypatch.setattr(slack, "_send", lambda _t: True)
    assert slack.notify("hello", bypass_debounce=True) is True
    monkeypatch.setattr(slack, "_send", lambda _t: False)
    assert slack.notify("hello", bypass_debounce=True) is False
    monkeypatch.setattr(slack, "_send", lambda _t: True)
    monkeypatch.setattr(slack, "_DEBOUNCE_WINDOW_SEC", 300)
    monkeypatch.setattr(slack, "_last_sent", {})
    assert slack.notify("dup") is True
    assert slack.notify("dup") is False                        # debounced
