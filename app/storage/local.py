"""Local order store — SQLite mirror of the Trading DB `stock_orders` shape.

The database lives at {storage_dir}/trading.db so any SQLite client (DBeaver,
sqlite3 CLI) can run the same query shapes as the real Trading DB:

    SELECT order_id, symbol, side, order_type, trigger_type, state,
           quantity, limit_price, stop_price, filled_quantity, average_price,
           created_at, updated_at, raw, ingested_at
    FROM stock_orders;

SQLite has no schemas, so drop the `public.` prefix. Fill semantics match
docs/sql/pnl_shapes.sql: a fill is any row with filled_quantity > 0 and a
non-null average_price, timestamped by COALESCE(updated_at, created_at).
"""

import json
import logging
import sqlite3
import uuid
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path

log = logging.getLogger(__name__)

_SAMPLES = Path(__file__).resolve().parent / "samples" / "stock_orders.json"

COLUMNS = [
    "order_id", "symbol", "side", "order_type", "trigger_type", "state",
    "quantity", "limit_price", "stop_price", "filled_quantity",
    "average_price", "created_at", "updated_at", "raw", "ingested_at",
]

_SCHEMA = """
CREATE TABLE IF NOT EXISTS stock_orders (
    order_id        TEXT PRIMARY KEY,
    symbol          TEXT NOT NULL,
    side            TEXT NOT NULL,
    order_type      TEXT,
    trigger_type    TEXT,
    state           TEXT,
    quantity        REAL,
    limit_price     REAL,
    stop_price      REAL,
    filled_quantity REAL,
    average_price   REAL,
    created_at      TEXT,
    updated_at      TEXT,
    raw             TEXT,
    ingested_at     TEXT
)
"""

_FILLS_QUERY = """
SELECT symbol, side, filled_quantity AS qty, average_price AS price,
       COALESCE(updated_at, created_at) AS ts
FROM stock_orders
WHERE filled_quantity > 0 AND average_price IS NOT NULL
ORDER BY ts
"""


def _storage_dir() -> Path:
    from app.config import Config
    if not Config.LOCAL_STORAGE_DIR:
        raise ValueError(
            "local storage used but no `storage_dir` configured for app mode "
            f"`{Config.APP_MODE}` — set it in configs/config.json"
        )
    return Path(Config.LOCAL_STORAGE_DIR)


def db_path() -> Path:
    return _storage_dir() / "trading.db"


def _parse_ts(raw) -> datetime:
    if isinstance(raw, datetime):
        return raw if raw.tzinfo else raw.replace(tzinfo=timezone.utc)
    return datetime.fromisoformat(str(raw).replace("Z", "+00:00"))


def _insert_orders(conn: sqlite3.Connection, rows: list[dict]) -> None:
    placeholders = ", ".join("?" for _ in COLUMNS)
    sql = f"INSERT INTO stock_orders ({', '.join(COLUMNS)}) VALUES ({placeholders})"
    conn.executemany(sql, [
        tuple(
            json.dumps(row.get(col), default=str) if col == "raw"
            else row.get(col)
            for col in COLUMNS
        )
        for row in rows
    ])


def _seed_if_empty(conn: sqlite3.Connection) -> None:
    if conn.execute("SELECT count(*) FROM stock_orders").fetchone()[0]:
        return
    rows = json.loads(_SAMPLES.read_text())
    _insert_orders(conn, rows)
    conn.commit()
    log.info("[storage] seeded %d sample orders into %s", len(rows), db_path())


def _connect() -> sqlite3.Connection:
    path = db_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute(_SCHEMA)
    _seed_if_empty(conn)
    return conn


def read_trade_fills() -> list[dict]:
    """Normalized fills for PnL replay, derived from stock_orders."""
    with closing(_connect()) as conn:
        return [
            {
                "symbol": row["symbol"],
                "side": row["side"],
                "qty": float(row["qty"]),
                "price": float(row["price"]),
                "ts": _parse_ts(row["ts"]),
            }
            for row in conn.execute(_FILLS_QUERY)
        ]


def write_trade_fills(fills: list[dict]) -> Path:
    """Snapshot normalized broker fills into stock_orders (full replace).

    Normalized fills only carry symbol/side/qty/price/ts, so order-level
    fields we can't know (order_type, trigger_type, limit/stop price) are
    left NULL; state is 'filled' by construction and raw holds the fill.
    """
    now = datetime.now(timezone.utc).isoformat()
    rows = []
    for fill in fills:
        ts = fill["ts"]
        ts = ts.isoformat() if hasattr(ts, "isoformat") else str(ts)
        rows.append({
            "order_id": uuid.uuid4().hex,
            "symbol": fill["symbol"],
            "side": fill["side"],
            "state": "filled",
            "quantity": fill["qty"],
            "filled_quantity": fill["qty"],
            "average_price": fill["price"],
            "created_at": ts,
            "updated_at": ts,
            "raw": {**fill, "ts": ts},
            "ingested_at": now,
        })
    with closing(_connect()) as conn, conn:
        conn.execute("DELETE FROM stock_orders")
        _insert_orders(conn, rows)
    log.info("[storage] wrote %d orders to %s", len(rows), db_path())
    return db_path()
