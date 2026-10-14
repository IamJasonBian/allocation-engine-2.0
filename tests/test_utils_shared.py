"""Engine-loop sweep gating: 3-hour cadence and designated MU/NBIS coverage."""

from datetime import datetime, timedelta, timezone

from app.risk.equity_stop_mapping import EQUITY_STOP_MAPPING
from app.stop_sweeper import StopStore
from app.utils_shared import SweepState, maybe_stop_sweep

from tests.test_stop_sweeper import FakeClient


def _config(store_path, **over):
    cfg = {
        "STOP_SWEEP_ENABLED": True,
        "AUTH_SERVICE_URL": "https://box.example",
        "RH_AUTH_SERVICE_REQUEST_TOKEN": "tok",
        "STOP_SWEEP_HOUR_ET": 0,
        "STOP_TICKERS": "",
        "STOP_SWEEP_DRY_RUN": True,
        "STOP_TRAIL_PERCENT": 16,
        "STOP_SWEEP_INTERVAL_SECONDS": 10800,
        "STOP_DB_PATH": store_path,
    }
    cfg.update(over)
    return cfg


def _state(store, client=None):
    state = SweepState()
    state.store = store
    state.client = client or FakeClient()
    return state


def test_maybe_stop_sweep_skips_inside_3h_window(tmp_path):
    store = StopStore(str(tmp_path / "stops.sqlite3"))
    store.set_meta("last_sweep_at", datetime.now(timezone.utc).isoformat())
    client = FakeClient()
    maybe_stop_sweep(_config(store.path), _state(store, client), [])
    assert client.rh_reads == 0


def test_maybe_stop_sweep_runs_again_after_3h(tmp_path):
    store = StopStore(str(tmp_path / "stops.sqlite3"))
    store.set_meta(
        "last_sweep_at",
        (datetime.now(timezone.utc) - timedelta(hours=3)).isoformat(),
    )
    client = FakeClient()
    maybe_stop_sweep(
        _config(store.path),
        _state(store, client),
        [{"symbol": "MU", "qty": 2, "market_value": 400}],
    )
    assert client.rh_reads == 1


def test_maybe_stop_sweep_includes_mapped_symbols(tmp_path, monkeypatch):
    captured = {}

    def fake_sweep(client, store, tickers, **kwargs):
        captured["tickers"] = list(tickers)
        return {
            "active_from_rh": 0, "placed": [], "simulated": [],
            "renewed": [], "pruned": [], "skipped": [],
            "unresolved_active_orders": [],
        }

    monkeypatch.setattr("app.utils_shared.sw.sweep", fake_sweep)
    store = StopStore(str(tmp_path / "stops.sqlite3"))
    maybe_stop_sweep(
        _config(store.path),
        _state(store),
        [{"symbol": "AAPL", "qty": 1, "market_value": 100}],
    )
    assert set(EQUITY_STOP_MAPPING) <= set(captured["tickers"])
    assert "AAPL" in captured["tickers"]
    assert {"MU", "NBIS"} <= set(captured["tickers"])
