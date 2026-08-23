"""Local order store — SQLite mirror of the Trading DB `stock_orders` shape.

The database lives at {storage_dir}/trading.db so any SQLite client (DBeaver,
sqlite3 CLI) can run the same query shapes as the real Trading DB:

    SELECT order_id, symbol, side, order_type, trigger_type, state,
           quantity, limit_price, stop_price, filled_quantity, average_price,
           created_at, updated_at, raw, ingested_at
    FROM stock_orders;

A `price_history` table (symbol, date, close) holds daily closes for the
risk/volatility series, seeded from samples alongside stock_orders.

`risk_profile.json` and `position_series.json` are local-only parse dumps (not
stores): flat ticker risk plus per-symbol `{daily, variance, monthlyGrowth}`.

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

_SAMPLES_DIR = Path(__file__).resolve().parent / "samples"
_SAMPLES = _SAMPLES_DIR / "stock_orders.json"
_PRICE_SAMPLES = _SAMPLES_DIR / "price_history.json"

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

_PRICE_SCHEMA = """
CREATE TABLE IF NOT EXISTS price_history (
    symbol TEXT NOT NULL,
    date   TEXT NOT NULL,
    close  REAL NOT NULL,
    PRIMARY KEY (symbol, date)
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


def risk_profile_path() -> Path:
    return _storage_dir() / "risk_profile.json"


def position_series_path() -> Path:
    return _storage_dir() / "position_series.json"


def today_walk_path() -> Path:
    return _storage_dir() / "today_walk.json"


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
    """Seed sample orders/closes; top up sample symbols a DB doesn't have yet.

    Inserts are keyed (order_id / symbol+date) with OR IGNORE, so a
    sample-seeded DB keeps its rows and only gains sample symbols added later.
    A DB holding a live snapshot (non-sample order ids) is left alone.
    """
    have = {r[0] for r in conn.execute("SELECT order_id FROM stock_orders")}
    if have and not all(oid.startswith("sample-") for oid in have):
        return  # live snapshot (write_trade_fills) — never mix samples back in
    rows = [r for r in json.loads(_SAMPLES.read_text()) if r["order_id"] not in have]
    if rows:
        _insert_orders(conn, rows)
        conn.commit()
        log.info("[storage] seeded %d sample orders into %s", len(rows), db_path())
    rows = json.loads(_PRICE_SAMPLES.read_text())
    cur = conn.executemany(
        "INSERT OR IGNORE INTO price_history (symbol, date, close) VALUES (?, ?, ?)",
        [(r["symbol"], r["date"], r["close"]) for r in rows],
    )
    if cur.rowcount:
        conn.commit()
        log.info("[storage] seeded %d sample closes into %s", cur.rowcount, db_path())


def _ticker_risk_flat(symbol: str, fills: list[dict], closes: list[dict]) -> tuple[dict, dict] | None:
    from app.pnl import format_ticker_risk, pnl_risk
    risk = pnl_risk(fills, closes, symbol)
    if "closeStdUsd" not in risk:
        return None
    return {
        "position": risk["position"],
        "closeStdUsd": risk["closeStdUsd"],
        "growthRatePct": risk["growthRatePct"],
        "growthRateMeanPct": risk["growthRateMeanPct"],
        "growthRateStdPct": risk["growthRateStdPct"],
        "riskAdjustedGrowthRate": risk["riskAdjustedGrowthRate"],
        "growthRateZ": risk["growthRateZ"],
        "riskUsd": risk["riskUsd"],
        "variance": risk.get("variance"),
        "monthlyGrowth": risk.get("monthlyGrowth") or [],
        "text": format_ticker_risk(symbol.upper(), risk),
    }, {
        "daily": risk.get("positionSeries") or [],
        "variance": risk.get("variance"),
        "monthlyGrowth": risk.get("monthlyGrowth") or [],
    }


def _portfolio_flat(tickers: dict) -> dict:
    from math import sqrt
    from app.pnl import format_portfolio_risk
    risks = [t["riskUsd"] for t in tickers.values()]
    row = {
        "symbols": sorted(tickers),
        "riskUsd": round(sum(risks), 2),
        "uncorrelatedRiskUsd": round(sqrt(sum(r ** 2 for r in risks)), 2) if risks else 0.0,
    }
    row["text"] = format_portfolio_risk(row)
    return row


def _write_position_series(series_by_symbol: dict) -> Path:
    path = position_series_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(series_by_symbol, indent=2) + "\n")
    return path


def _build_profile(fills: list[dict], closes_by_symbol: dict[str, list[dict]]) -> dict:
    profile = {}
    series_by_symbol = {}
    symbols = {f["symbol"].upper() for f in fills} | {s.upper() for s in closes_by_symbol}
    for sym in sorted(symbols):
        pair = _ticker_risk_flat(sym, fills, closes_by_symbol.get(sym, []))
        if not pair:
            continue
        row, series = pair
        profile[sym] = row
        series_by_symbol[sym] = series
    if profile:
        profile["portfolio"] = _portfolio_flat(profile)
        _write_position_series(series_by_symbol)
    return profile


def _profile_from_conn(conn: sqlite3.Connection) -> dict:
    fills = [
        {
            "symbol": row["symbol"],
            "side": row["side"],
            "qty": float(row["qty"]),
            "price": float(row["price"]),
            "ts": _parse_ts(row["ts"]),
        }
        for row in conn.execute(_FILLS_QUERY)
    ]
    closes_by_symbol = {}
    for sym in {f["symbol"].upper() for f in fills}:
        closes_by_symbol[sym] = [
            {"date": row["date"], "close": float(row["close"])}
            for row in conn.execute(
                "SELECT date, close FROM price_history WHERE symbol = ? ORDER BY date",
                (sym,),
            )
        ]
    return _build_profile(fills, closes_by_symbol)


def _write_risk_json(profile: dict) -> Path:
    path = risk_profile_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(profile, indent=2) + "\n")
    return path


def _connect() -> sqlite3.Connection:
    path = db_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute(_SCHEMA)
    conn.execute(_PRICE_SCHEMA)
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


def read_price_history(symbol: str) -> list[dict]:
    """Daily closes for one symbol, ascending by date."""
    with closing(_connect()) as conn:
        return [
            {"date": row["date"], "close": float(row["close"])}
            for row in conn.execute(
                "SELECT date, close FROM price_history WHERE symbol = ? ORDER BY date",
                (symbol.upper(),),
            )
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


def write_price_history(symbol: str, closes: list[dict]) -> None:
    """Replace daily closes for one symbol."""
    sym = symbol.upper()
    with closing(_connect()) as conn, conn:
        conn.execute("DELETE FROM price_history WHERE symbol = ?", (sym,))
        conn.executemany(
            "INSERT INTO price_history (symbol, date, close) VALUES (?, ?, ?)",
            [(sym, row["date"], float(row["close"])) for row in closes],
        )


def rebuild_artifacts() -> dict:
    """Rebuild risk_profile.json and position_series.json from local SQLite."""
    with closing(_connect()) as conn:
        profile = _profile_from_conn(conn)
    if profile:
        _write_risk_json(profile)
    return profile


def sync_from_broker(broker) -> dict:
    """Pull live fills + closes from the broker, persist, rebuild JSON dumps."""
    from app.storage.fills import save_trade_fills
    save_trade_fills(broker)
    fills = read_trade_fills()
    for sym in sorted({f["symbol"].upper() for f in fills}):
        write_price_history(sym, broker._fetch_live_price_history(sym))
    return rebuild_artifacts()


def _yahoo_symbol(symbol: str) -> str:
    return symbol.replace(".", "-")


def fetch_daily_closes(symbol: str, range_: str = "1y") -> tuple[list[str], list[float]]:
    """Yahoo daily closes. Does not map BTC → BTC-USD (this book is the ETF)."""
    import urllib.request
    ysym = _yahoo_symbol(symbol)
    url = (
        f"https://query1.finance.yahoo.com/v8/finance/chart/{ysym}"
        f"?range={range_}&interval=1d"
    )
    req = urllib.request.Request(url, headers={"User-Agent": "allocation-engine/2.0"})
    with urllib.request.urlopen(req, timeout=15) as resp:
        body = json.loads(resp.read().decode())
    result = (body.get("chart") or {}).get("result") or []
    if not result:
        return [], []
    row = result[0]
    ts = row.get("timestamp") or []
    raw = ((row.get("indicators") or {}).get("quote") or [{}])[0].get("close") or []
    dates, closes = [], []
    for t, c in zip(ts, raw):
        if c is None:
            continue
        dates.append(datetime.fromtimestamp(t, tz=timezone.utc).date().isoformat())
        closes.append(float(c))
    return dates, closes


def rebuild_risk_from_today_walk(walk: dict | None = None) -> dict:
    """Fill risk_profile.json from the live today-walk + Yahoo closes."""
    from app.pnl import format_ticker_risk, risk_from_today_walk
    from app.trading_db import today_walk_from_db
    walk = walk or today_walk_from_db()
    write_today_walk(walk)
    profile = {}
    series_by_symbol = {}
    missing = []
    for sym, row in walk["tickers"].items():
        try:
            dates, closes = fetch_daily_closes(sym)
        except Exception as exc:
            log.warning("[storage] yahoo closes failed for %s: %s", sym, exc)
            missing.append(sym)
            continue
        if len(closes) < 3:
            missing.append(sym)
            continue
        risk = risk_from_today_walk(row, closes, dates=dates)
        if "riskUsd" not in risk:
            missing.append(sym)
            continue
        profile[sym] = {
            "position": risk["position"],
            "closeStdUsd": risk["closeStdUsd"],
            "growthRatePct": risk["growthRatePct"],
            "growthRateMeanPct": risk["growthRateMeanPct"],
            "growthRateStdPct": risk["growthRateStdPct"],
            "riskAdjustedGrowthRate": risk["riskAdjustedGrowthRate"],
            "growthRateZ": risk.get("growthRateZ"),
            "riskUsd": risk["riskUsd"],
            "variance": risk.get("variance"),
            "monthlyGrowth": risk.get("monthlyGrowth") or [],
            "sodQty": row["sodQty"],
            "nowQty": row["nowQty"],
            "last": row["last"],
            "text": format_ticker_risk(sym, risk),
        }
        series_by_symbol[sym] = {
            "daily": risk.get("positionSeries") or [],
            "variance": risk.get("variance"),
            "monthlyGrowth": risk.get("monthlyGrowth") or [],
        }
    if profile:
        profile["portfolio"] = _portfolio_flat(
            {k: v for k, v in profile.items() if k != "portfolio"}
        )
        if missing:
            profile["portfolio"]["missing"] = missing
        _write_risk_json(profile)
        _write_position_series(series_by_symbol)
    return profile


def write_today_walk(payload: dict) -> Path:
    path = today_walk_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2) + "\n")
    return path


def read_risk_profile() -> dict:
    """Flat ticker-risk JSON: {symbol: {position, closeStdUsd, ...}}."""
    path = risk_profile_path()
    if not path.exists():
        rebuild_artifacts()
    if not path.exists():
        return {}
    return json.loads(path.read_text())


