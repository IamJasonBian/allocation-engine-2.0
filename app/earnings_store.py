"""SQLite storage interface for EPS values.

Same shape as `stop_sweeper.StopStore`: a keyed table plus a `meta` sidecar, in
the gitignored `data/` dir. Storage only — no fetching, no analytics.

One row per ticker-quarter. A reported quarter has both estimate and actual; an
upcoming quarter has the estimate and a null actual, so current and historical
live in the same table.
"""

from __future__ import annotations

import json
import os
import sqlite3
from datetime import date

# Env read directly (same as stop_sweeper.py) so the storage layer stays
# importable without Flask — app/__init__.py pulls in the whole web app.
DEFAULT_DB = os.getenv(
    "EPS_DB_PATH",
    os.path.join(os.path.dirname(__file__), "..", "data", "eps.sqlite3"),
)
DEFAULT_JSON = os.getenv(
    "EPS_JSON_PATH",
    os.path.join(os.path.dirname(__file__), "..", "data", "eps.json"),
)

SCHEMA = """
CREATE TABLE IF NOT EXISTS eps (
  ticker         TEXT NOT NULL,
  earnings_date  TEXT NOT NULL,   -- ISO
  eps_estimate   REAL,
  eps_actual     REAL,            -- NULL until the quarter is reported
  status         TEXT NOT NULL,   -- 'reported' | 'upcoming'
  updated_at     TEXT,
  PRIMARY KEY (ticker, earnings_date)
);
CREATE INDEX IF NOT EXISTS idx_eps_date ON eps(earnings_date);
CREATE INDEX IF NOT EXISTS idx_eps_status ON eps(status, earnings_date);
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT);
"""


class EarningsStore:
    """Keyed store for EPS rows."""

    def __init__(self, path: str | None = None):
        self.path = path or DEFAULT_DB
        os.makedirs(os.path.dirname(os.path.abspath(self.path)), exist_ok=True)
        self.db = sqlite3.connect(self.path)
        self.db.row_factory = sqlite3.Row
        self.db.executescript(SCHEMA)

    # -- write ---------------------------------------------------------------

    def upsert_many(self, records: list[dict]) -> int:
        """Insert or update EPS rows.

        An upcoming quarter becomes a reported one in place when the actual
        lands, so the key is (ticker, date) and never the status.

        Args:
            records: dicts with ticker, earnings_date, eps_estimate,
                eps_actual, status.

        Returns:
            Number of rows written.
        """
        stamp = date.today().isoformat()
        rows = [
            (r["ticker"], r["earnings_date"], r.get("eps_estimate"),
             r.get("eps_actual"),
             r.get("status") or ("reported" if r.get("eps_actual") is not None
                                 else "upcoming"),
             stamp)
            for r in records
        ]
        self.db.executemany(
            """INSERT INTO eps(ticker, earnings_date, eps_estimate, eps_actual,
                               status, updated_at)
               VALUES(?,?,?,?,?,?)
               ON CONFLICT(ticker, earnings_date) DO UPDATE SET
                 eps_estimate=excluded.eps_estimate,
                 eps_actual=excluded.eps_actual,
                 status=excluded.status,
                 updated_at=excluded.updated_at""",
            rows)
        self.db.commit()
        return len(rows)

    def set_meta(self, key: str, value: str) -> None:
        self.db.execute("INSERT INTO meta(key,value) VALUES(?,?) "
                        "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                        (key, value))
        self.db.commit()

    # -- read ----------------------------------------------------------------

    def get_meta(self, key: str) -> str | None:
        row = self.db.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
        return row["value"] if row else None

    def all(self) -> list[dict]:
        """Every row, ticker then date."""
        return [dict(r) for r in self.db.execute(
            "SELECT ticker, earnings_date, eps_estimate, eps_actual, status "
            "FROM eps ORDER BY ticker, earnings_date")]

    def tickers(self) -> list[str]:
        return [r["ticker"] for r in self.db.execute(
            "SELECT DISTINCT ticker FROM eps ORDER BY ticker")]

    def count(self) -> int:
        return self.db.execute("SELECT COUNT(*) c FROM eps").fetchone()["c"]

    # -- export --------------------------------------------------------------

    def export_json(self, path: str | None = None) -> str:
        """Write the dataset allocation-gym-2.0 reads.

        Args:
            path: destination; defaults to $EPS_JSON_PATH.

        Returns:
            The absolute path written.
        """
        path = path or DEFAULT_JSON
        os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
        payload = {
            "generated": date.today().isoformat(),
            "tickers": self.tickers(),
            "eps": self.all(),
        }
        with open(path, "w") as f:
            json.dump(payload, f, indent=2)
        return os.path.abspath(path)

    def close(self) -> None:
        self.db.close()
