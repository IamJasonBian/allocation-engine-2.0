"""Local trade-fill storage + mode-based config loader."""

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

import pytest

from app.app_config import load_app_config
from app.config import Config
from app.storage import local


@pytest.fixture
def storage_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(Config, "LOCAL_STORAGE_DIR", str(tmp_path))
    return tmp_path


def test_read_seeds_from_samples(storage_dir):
    fills = local.read_trade_fills()
    assert len(fills) == 12
    assert fills[0]["symbol"] == "BTC"
    assert fills[0]["ts"].tzinfo is not None
    assert (storage_dir / "trading.db").exists()


def test_write_roundtrip(storage_dir):
    ts = datetime(2026, 1, 1, tzinfo=timezone.utc)
    local.write_trade_fills([
        {"symbol": "AAA", "side": "BUY", "qty": 1, "price": 10.0, "ts": ts},
    ])
    fills = local.read_trade_fills()
    assert len(fills) == 1
    assert fills[0]["symbol"] == "AAA"
    assert fills[0]["ts"] == ts


def test_price_history_seeds_and_reads(storage_dir):
    closes = local.read_price_history("btc")
    assert len(closes) == 120
    assert closes[0]["date"] == "2026-04-22"
    assert closes[0]["close"] == 42000.0
    assert closes == sorted(closes, key=lambda c: c["date"])
    assert local.read_price_history("UNKNOWN") == []


def test_risk_profile_seeds_from_fills(storage_dir):
    from app.pnl import format_ticker_risk, pnl_risk
    local.read_trade_fills()  # seeds orders + closes, then risk JSON
    path = storage_dir / "risk_profile.json"
    payload = json.loads(path.read_text())
    expected = pnl_risk(local.read_trade_fills(), local.read_price_history("BTC"), "BTC")
    btc = {
        "position": expected["position"],
        "closeStdUsd": expected["closeStdUsd"],
        "growthRatePct": expected["growthRatePct"],
        "growthRateMeanPct": expected["growthRateMeanPct"],
        "growthRateStdPct": expected["growthRateStdPct"],
        "riskAdjustedGrowthRate": expected["riskAdjustedGrowthRate"],
        "growthRateZ": expected["growthRateZ"],
        "riskUsd": expected["riskUsd"],
        "variance": expected["variance"],
        "monthlyGrowth": expected["monthlyGrowth"],
        "text": format_ticker_risk("BTC", expected),
    }
    assert payload["BTC"] == btc
    assert "BTC" in payload["portfolio"]["symbols"]
    ticker_risk = sum(
        payload[s]["riskUsd"] for s in payload["portfolio"]["symbols"]
    )
    assert payload["portfolio"]["riskUsd"] == pytest.approx(ticker_risk, abs=0.01)
    assert local.read_risk_profile() == payload


def test_stock_orders_queryable_in_trading_db_shape(storage_dir):
    """The exact Trading DB column list works against the local SQLite db."""
    local.read_trade_fills()  # triggers seed
    with sqlite3.connect(storage_dir / "trading.db") as conn:
        rows = conn.execute(
            "SELECT order_id, symbol, side, order_type, trigger_type, state,"
            " quantity, limit_price, stop_price, filled_quantity, average_price,"
            " created_at, updated_at, raw, ingested_at FROM stock_orders"
        ).fetchall()
    assert len(rows) == 12
    order_ids = {r[0] for r in rows}
    assert order_ids == {f"sample-{i:04d}" for i in range(1, 13)}
    assert all(json.loads(r[13]) is not None for r in rows)  # raw column is JSON


# --------------------------------------------------------------------------- #
# configs/config.json loader — walmart-data-pipeline style modes
# --------------------------------------------------------------------------- #

def _config_file(tmp_path, body):
    path = tmp_path / "config.json"
    path.write_text(json.dumps(body))
    return path


_VALID = {
    "app_mode": "dev",
    "dev": {"env_file": ".env.local", "storage_backend": "local",
            "storage_dir": "./data/local"},
    "prod": {"env_file": ".env", "storage_backend": "broker"},
}


def test_load_config_resolves_mode_and_relative_dir(tmp_path, monkeypatch):
    monkeypatch.delenv("APP_MODE", raising=False)
    cfg = load_app_config(_config_file(tmp_path, _VALID))
    assert cfg.app_mode == "dev"
    assert cfg.mode.storage_backend == "local"
    assert Path(cfg.mode.storage_dir).is_absolute()


def test_app_mode_env_overrides_json(tmp_path, monkeypatch):
    monkeypatch.setenv("APP_MODE", "prod")
    cfg = load_app_config(_config_file(tmp_path, _VALID))
    assert cfg.app_mode == "prod"
    assert cfg.mode.storage_backend == "broker"
    assert cfg.mode.storage_dir is None  # broker mode may omit storage_dir


def test_local_backend_requires_storage_dir(tmp_path, monkeypatch):
    monkeypatch.delenv("APP_MODE", raising=False)
    bad = dict(_VALID, dev={"env_file": ".env.local", "storage_backend": "local"})
    with pytest.raises(ValueError, match="storage_dir"):
        load_app_config(_config_file(tmp_path, bad))


def test_invalid_mode_rejected(tmp_path, monkeypatch):
    monkeypatch.delenv("APP_MODE", raising=False)
    bad = dict(_VALID, app_mode="staging")
    with pytest.raises(ValueError, match="Invalid app mode"):
        load_app_config(_config_file(tmp_path, bad))


def test_missing_mode_key_rejected(tmp_path, monkeypatch):
    monkeypatch.delenv("APP_MODE", raising=False)
    bad = dict(_VALID, dev={"env_file": ".env.local"})
    with pytest.raises(ValueError, match="storage_backend"):
        load_app_config(_config_file(tmp_path, bad))
