"""Local trade-fill storage + mode-based config loader."""

import json
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
    assert len(fills) == 4
    assert fills[0]["symbol"] == "BTC"
    assert fills[0]["ts"].tzinfo is not None
    assert (storage_dir / "trade_fills.json").exists()


def test_write_roundtrip(storage_dir):
    ts = datetime(2026, 1, 1, tzinfo=timezone.utc)
    local.write_trade_fills([
        {"symbol": "AAA", "side": "BUY", "qty": 1, "price": 10.0, "ts": ts},
    ])
    fills = local.read_trade_fills()
    assert len(fills) == 1
    assert fills[0]["symbol"] == "AAA"
    assert fills[0]["ts"] == ts


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
