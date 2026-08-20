"""Local fill store — read/write normalized trade fills as JSON in the data directory."""

import json
import logging
import shutil
from datetime import datetime, timezone
from pathlib import Path

log = logging.getLogger(__name__)

_SAMPLES = Path(__file__).resolve().parent / "samples" / "trade_fills.json"


def _storage_dir() -> Path:
    from app.config import Config
    if not Config.LOCAL_STORAGE_DIR:
        raise ValueError(
            "local storage used but no `storage_dir` configured for app mode "
            f"`{Config.APP_MODE}` — set it in configs/config.json"
        )
    return Path(Config.LOCAL_STORAGE_DIR)


def trade_fills_path() -> Path:
    return _storage_dir() / "trade_fills.json"


def _parse_ts(raw) -> datetime:
    if isinstance(raw, datetime):
        return raw if raw.tzinfo else raw.replace(tzinfo=timezone.utc)
    return datetime.fromisoformat(str(raw).replace("Z", "+00:00"))


def _serialize_fill(fill: dict) -> dict:
    ts = fill["ts"]
    if hasattr(ts, "isoformat"):
        ts = ts.isoformat()
    return {
        "symbol": fill["symbol"],
        "side": fill["side"],
        "qty": fill["qty"],
        "price": fill["price"],
        "ts": ts,
    }


def ensure_seed() -> Path:
    """Create data/local/ and seed trade_fills.json from samples if missing."""
    path = trade_fills_path()
    if path.exists():
        return path
    path.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy(_SAMPLES, path)
    log.info("[storage] seeded %s from samples", path)
    return path


def read_trade_fills() -> list[dict]:
    path = ensure_seed()
    raw = json.loads(path.read_text())
    fills = []
    for row in raw:
        fills.append({
            "symbol": row["symbol"],
            "side": row["side"],
            "qty": float(row["qty"]),
            "price": float(row["price"]),
            "ts": _parse_ts(row["ts"]),
        })
    return fills


def write_trade_fills(fills: list[dict]) -> Path:
    path = trade_fills_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps([_serialize_fill(f) for f in fills], indent=2) + "\n")
    log.info("[storage] wrote %d fills to %s", len(fills), path)
    return path
