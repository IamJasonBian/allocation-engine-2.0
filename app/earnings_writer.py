"""Write-path client for EPS values — fetch, trim, persist, export.

Same role as `trading_db.py`: this repo owns the write path. It fetches EPS
values, keeps a fixed window, and writes them to `EarningsStore`, which exports
the JSON dataset allocation-gym-2.0 reads. Nothing here reads back for analysis.

Retention is HISTORY_QUARTERS reported quarters per ticker plus every upcoming
one, so the dataset carries actuals-vs-estimates for recent history and the
current estimate for what has not printed yet.

yfinance is not in requirements.txt — it is only needed to refresh, not to
serve, so it is imported lazily and the store works without it.
"""

from __future__ import annotations

import logging
import os
from datetime import date

from app.earnings_store import EarningsStore

log = logging.getLogger(__name__)

HISTORY_QUARTERS = 4    # reported quarters retained per ticker
FETCH_QUARTERS = 12     # requested per ticker; trimmed down to the window above

# AI-infra cohort. Env-overridable, same pattern as STOP_TICKERS.
DEFAULT_TICKERS = [t.strip().upper() for t in os.getenv(
    "EPS_TICKERS",
    "NVDA,AVGO,MU,MRVL,AMD,TSM,ARM,ALAB,CRDO,"
    "ANET,FTNT,CIEN,COHR,"
    "DELL,HPE,NTAP,SMCI,P,"
    "VRT,CRWV,NBIS,"
    "ORCL,SNOW,NET,DDOG,MDB",
).split(",") if t.strip()]


def classify(earnings_date: str, eps_actual: float | None, as_of: str) -> str:
    """Label a quarter reported / upcoming / missing.

    Null-ness of the actual alone is not enough: Yahoo returns old quarters it
    has no figure for, and treating those as "upcoming" makes the next-report
    lookup return a date from 2021.

    Args:
        earnings_date: ISO date of the report.
        eps_actual: reported EPS, or None.
        as_of: ISO date to judge past-vs-future against.

    Returns:
        'reported' (has an actual), 'upcoming' (future, awaiting one), or
        'missing' (past, no figure ever published).
    """
    if eps_actual is not None:
        return "reported"
    return "upcoming" if earnings_date >= as_of else "missing"


def _num(value) -> float | None:
    """Coerce a pandas cell to float; NaN and None both become None."""
    if value is None:
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return None if number != number else number   # NaN is the only self-unequal float


def trim(records: list[dict], history_quarters: int = HISTORY_QUARTERS) -> list[dict]:
    """Keep the last N reported quarters per ticker, plus all upcoming ones.

    Upcoming quarters are never trimmed — there are only ever one or two, and
    they are the current estimate the dataset exists to carry. Quarters marked
    'missing' are dropped entirely.

    Args:
        records: EPS records for any number of tickers.
        history_quarters: reported quarters to retain per ticker.

    Returns:
        The retained records, ticker then date.
    """
    by_ticker: dict[str, list[dict]] = {}
    for record in records:
        by_ticker.setdefault(record["ticker"], []).append(record)

    kept: list[dict] = []
    for rows in by_ticker.values():
        reported = sorted(
            (r for r in rows if r["status"] == "reported"),
            key=lambda r: r["earnings_date"],
        )
        # 'missing' rows are dropped: a past quarter with no figure carries no
        # estimate-vs-actual pair and would pollute the next-report lookup.
        upcoming = sorted(
            (r for r in rows if r["status"] == "upcoming"),
            key=lambda r: r["earnings_date"],
        )
        kept.extend(reported[-history_quarters:] if history_quarters else reported)
        kept.extend(upcoming)

    kept.sort(key=lambda r: (r["ticker"], r["earnings_date"]))
    return kept


def fetch(
    tickers: list[str],
    quarters: int = FETCH_QUARTERS,
    as_of: str | None = None,
) -> tuple[list[dict], list[dict]]:
    """Pull EPS estimate/actual per quarter for each ticker.

    One bad symbol must not cost the run, so failures are returned rather than
    raised — a partial refresh is still worth persisting.

    Args:
        tickers: symbols to fetch.
        quarters: quarters to request per ticker before trimming.
        as_of: ISO date used to split past from future; defaults to today.

    Returns:
        (records, failures). Records carry ticker, earnings_date,
        eps_estimate, eps_actual, status ('reported' | 'upcoming').

    Raises:
        RuntimeError: if yfinance is not installed.
    """
    try:
        import yfinance as yf
    except ImportError as e:
        raise RuntimeError(
            "yfinance required to refresh EPS: pip install yfinance"
        ) from e

    as_of = as_of or date.today().isoformat()
    records: list[dict] = []
    failures: list[dict] = []

    for ticker in tickers:
        try:
            frame = yf.Ticker(ticker).get_earnings_dates(limit=quarters)
        except Exception as e:  # noqa: BLE001 — one bad symbol, not the run
            log.warning("[eps] %s failed: %s", ticker, e)
            failures.append({"ticker": ticker, "error": str(e)[:200]})
            continue

        if frame is None or frame.empty:
            failures.append({"ticker": ticker, "error": "no data returned"})
            continue

        for timestamp, row in frame.iterrows():
            actual = _num(row.get("Reported EPS"))
            when = timestamp.date().isoformat()
            records.append({
                "ticker": ticker,
                "earnings_date": when,
                "eps_estimate": _num(row.get("EPS Estimate")),
                "eps_actual": actual,
                "status": classify(when, actual, as_of),
            })

    return records, failures


def refresh(
    tickers: list[str] | None = None,
    store: EarningsStore | None = None,
    history_quarters: int = HISTORY_QUARTERS,
) -> dict:
    """Fetch, trim, persist, and export in one pass.

    Args:
        tickers: symbols to refresh; defaults to DEFAULT_TICKERS.
        store: an open store, or None to open the configured one.
        history_quarters: reported quarters to retain per ticker.

    Returns:
        Summary with written, tickers, failures, and the exported path.
    """
    tickers = tickers or DEFAULT_TICKERS
    store = store or EarningsStore()

    records, failures = fetch(tickers)
    kept = trim(records, history_quarters)
    written = store.upsert_many(kept)

    store.set_meta("last_refresh", date.today().isoformat())
    store.set_meta("history_quarters", str(history_quarters))
    exported = store.export_json()

    log.info("[eps] %d rows across %d tickers -> %s (failed: %s)",
             written, len(store.tickers()), exported,
             ", ".join(f["ticker"] for f in failures) or "none")

    return {"written": written, "tickers": store.tickers(),
            "failures": failures, "exported": exported}
