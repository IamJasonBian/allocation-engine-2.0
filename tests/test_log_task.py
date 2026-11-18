"""Worker-lifetime log_task_id for the gunicorn engine loop.

One id per process: minted when the core loop starts, kept until the
gunicorn worker is restarted. Every log record carries it as log_task_id.
"""

import logging

from app import log_task


def setup_function():
    log_task.reset_task_id()


def test_ensure_task_id_mints_once_and_keeps_it():
    first = log_task.ensure_task_id()
    second = log_task.ensure_task_id()
    assert first and first == second
    assert first == log_task.get_task_id()


def test_reset_then_ensure_mints_a_new_id():
    first = log_task.ensure_task_id()
    log_task.reset_task_id()
    assert log_task.get_task_id() is None
    second = log_task.ensure_task_id()
    assert second and second != first


def test_log_records_include_bound_log_task_id():
    tid = log_task.ensure_task_id()
    record = logging.LogRecord(
        name="test", level=logging.INFO, pathname="", lineno=0,
        msg="hello", args=(), exc_info=None)
    assert log_task.TaskIdFilter().filter(record) is True
    assert record.log_task_id == tid


def test_log_record_without_task_id_uses_placeholder():
    record = logging.LogRecord(
        name="test", level=logging.INFO, pathname="", lineno=0,
        msg="hello", args=(), exc_info=None)
    log_task.TaskIdFilter().filter(record)
    assert record.log_task_id == "-"


def test_run_engine_loop_binds_task_id_before_first_tick(monkeypatch):
    """core_loop mints the worker id once, before it talks to the broker."""
    from types import SimpleNamespace
    from app import background

    seen = {}

    def fake_get_broker(_name):
        seen["task_id"] = log_task.get_task_id()
        raise SystemExit

    monkeypatch.setattr("app.brokers.get_broker", fake_get_broker)
    app = SimpleNamespace(config={
        "POLL_INTERVAL_SECONDS": 1,
        "DRY_RUN": True,
        "TRADING_DB_SYNC_SECONDS": 900,
        "RH_RETRY_HOUR_ET": 11,
        "ENGINE_BROKER": "robinhood",
        "STOP_SWEEP_ENABLED": False,
        "STOP_SWEEP_DRY_RUN": True,
    })
    try:
        background.run_engine_loop(app)
    except SystemExit:
        pass
    assert seen["task_id"]
    assert seen["task_id"] == log_task.get_task_id()
