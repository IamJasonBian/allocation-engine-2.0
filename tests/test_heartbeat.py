"""Resilient run loop + heartbeat for the unattended (remote) engine.

No engine, no gateway: a FakeEngine drives tick_and_report through success and
failure so the state machine (streak, backoff, heartbeat status) is what's
under test — the part the remote box depends on to survive gateway outages.
"""

import json

from app.heartbeat import write_heartbeat
from main import MAX_BACKOFF_SECONDS, tick_and_report


class FakeEngine:
    def __init__(self, fail=None):
        self.fail = fail            # exception to raise, or None for success
        self.ticks = 0

    def tick(self):
        self.ticks += 1
        if self.fail:
            raise self.fail


def _read(path):
    with open(path) as f:
        return json.load(f)


# -- heartbeat writer --------------------------------------------------------

def test_heartbeat_written_with_fields(tmp_path):
    p = str(tmp_path / "hb.json")
    write_heartbeat(p, status="ok", tick_count=3, consecutive_errors=0)
    d = _read(p)
    assert d["status"] == "ok" and d["tick_count"] == 3
    assert d["consecutive_errors"] == 0 and "updated_at" in d


# -- tick_and_report state machine -------------------------------------------

def test_success_increments_tick_resets_streak_returns_interval(tmp_path):
    hb = str(tmp_path / "hb.json")
    state = {"tick_count": 0, "consecutive_errors": 2}   # recovering
    sleep = tick_and_report(FakeEngine(), state, 30, heartbeat_path=hb)
    assert sleep == 30
    assert state["tick_count"] == 1 and state["consecutive_errors"] == 0
    assert _read(hb)["status"] == "ok"


def test_connection_error_streaks_and_backs_off(tmp_path):
    hb = str(tmp_path / "hb.json")
    state = {"tick_count": 5, "consecutive_errors": 0}
    s1 = tick_and_report(FakeEngine(ConnectionError("gw down")), state, 30, heartbeat_path=hb)
    assert state["consecutive_errors"] == 1 and s1 == 60
    s2 = tick_and_report(FakeEngine(ConnectionError("gw down")), state, 30, heartbeat_path=hb)
    assert state["consecutive_errors"] == 2 and s2 == 120
    d = _read(hb)
    assert d["status"] == "error" and "ConnectionError" in d["last_error"]
    assert d["tick_count"] == 5             # unchanged on failure


def test_backoff_is_capped(tmp_path):
    hb = str(tmp_path / "hb.json")
    state = {"tick_count": 0, "consecutive_errors": 10}
    sleep = tick_and_report(FakeEngine(ConnectionError("x")), state, 30, heartbeat_path=hb)
    assert sleep == MAX_BACKOFF_SECONDS


def test_generic_exception_never_raises(tmp_path):
    hb = str(tmp_path / "hb.json")
    state = {"tick_count": 0, "consecutive_errors": 0}
    sleep = tick_and_report(FakeEngine(ValueError("boom")), state, 30, heartbeat_path=hb)
    assert state["consecutive_errors"] == 1 and sleep == 60
    assert _read(hb)["last_error"].startswith("ValueError")
