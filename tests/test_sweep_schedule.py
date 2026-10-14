"""STOP_SWEEP_HOURS_ET: the stop sweep runs once per ET slot, not once a day."""

from datetime import datetime, timezone
from zoneinfo import ZoneInfo

import pytest

from app import utils_shared
from app.stop_sweeper import StopStore

ET = ZoneInfo("America/New_York")


def _freeze(monkeypatch, et_dt):
    class Frozen(datetime):
        @classmethod
        def now(cls, tz=None):
            return et_dt.astimezone(tz) if tz else et_dt.replace(tzinfo=None)
    monkeypatch.setattr(utils_shared, "datetime", Frozen)


@pytest.fixture
def run(monkeypatch, tmp_path):
    calls = []
    state = utils_shared.SweepState()
    state.store = StopStore(str(tmp_path / "stops.sqlite3"))
    state.client = object()

    def fake_sweep(*a, **k):
        # Like the real sweep(): latch last_sweep_at at "now".
        calls.append(1)
        state.store.set_meta("last_sweep_at",
                             utils_shared.datetime.now(timezone.utc).isoformat())
        return {"active_from_rh": 0, "placed": [], "simulated": [], "renewed": [],
                "pruned": [], "skipped": [], "unresolved_active_orders": []}

    monkeypatch.setattr(utils_shared.sw, "sweep", fake_sweep)
    monkeypatch.setattr(utils_shared.sw, "account_url_from_box", lambda: "")
    config = {"STOP_SWEEP_ENABLED": True, "AUTH_SERVICE_URL": "x",
              "RH_AUTH_SERVICE_REQUEST_TOKEN": "x", "STOP_SWEEP_HOURS_ET": [9, 12, 15],
              "STOP_TICKERS": "", "STOP_SWEEP_DRY_RUN": True, "STOP_TRAIL_PERCENT": 16}

    def tick(et_dt):
        _freeze(monkeypatch, et_dt)
        utils_shared.maybe_stop_sweep(config, state, [])
        return len(calls)
    return tick


def test_three_slots_a_day(run):
    day = datetime(2026, 10, 14, tzinfo=ET)
    assert run(day.replace(hour=8, minute=30)) == 0   # before first slot
    assert run(day.replace(hour=9, minute=1)) == 1
    assert run(day.replace(hour=11, minute=59)) == 1  # 9:00 slot already done
    assert run(day.replace(hour=12, minute=0)) == 2
    assert run(day.replace(hour=14)) == 2
    assert run(day.replace(hour=15, minute=5)) == 3
    assert run(day.replace(hour=23)) == 3
    assert run(day.replace(day=15, hour=9)) == 4      # next day's first slot


def test_missed_slots_run_once(run):
    """A worker booting mid-afternoon catches up with a single sweep."""
    day = datetime(2026, 10, 14, tzinfo=ET)
    assert run(day.replace(hour=16)) == 1
    assert run(day.replace(hour=17)) == 1
