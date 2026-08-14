"""Tests for the EPS storage interface and the write path.

No network: `trim` and the store are exercised directly with hand-built records.
"""

import json

import pytest

from app.earnings_store import EarningsStore
from app.earnings_writer import classify, trim


@pytest.fixture
def store(tmp_path):
    s = EarningsStore(str(tmp_path / "eps.sqlite3"))
    yield s
    s.close()


def _rec(ticker, date, est, actual):
    return {"ticker": ticker, "earnings_date": date, "eps_estimate": est,
            "eps_actual": actual,
            "status": "reported" if actual is not None else "upcoming"}


# --------------------------------------------------------------------------- #
# retention window
# --------------------------------------------------------------------------- #

def test_trim_keeps_the_last_four_reported_quarters():
    records = [_rec("X", f"2025-{m:02d}-01", 1.0, 1.1) for m in range(1, 9)]
    kept = trim(records, history_quarters=4)
    assert [r["earnings_date"] for r in kept] == [
        "2025-05-01", "2025-06-01", "2025-07-01", "2025-08-01"]


def test_trim_never_drops_upcoming_quarters():
    records = [_rec("X", f"2025-{m:02d}-01", 1.0, 1.1) for m in range(1, 9)]
    records.append(_rec("X", "2026-09-02", 3.24, None))
    kept = trim(records, history_quarters=4)
    upcoming = [r for r in kept if r["status"] == "upcoming"]
    assert len(upcoming) == 1
    assert upcoming[0]["eps_estimate"] == 3.24


def test_trim_windows_each_ticker_independently():
    records = ([_rec("A", f"2025-{m:02d}-01", 1.0, 1.1) for m in range(1, 9)]
               + [_rec("B", "2025-01-01", 2.0, 2.1)])
    kept = trim(records, history_quarters=4)
    assert sum(1 for r in kept if r["ticker"] == "A") == 4
    assert sum(1 for r in kept if r["ticker"] == "B") == 1


def test_trim_keeps_both_sides_of_each_historical_quarter():
    """Estimate and actual both survive — that pairing is the point."""
    kept = trim([_rec("X", "2025-01-01", 1.00, 1.10)], history_quarters=4)
    assert kept[0]["eps_estimate"] == 1.00
    assert kept[0]["eps_actual"] == 1.10


def test_trim_of_empty_input_is_empty():
    assert trim([]) == []


# --------------------------------------------------------------------------- #
# store
# --------------------------------------------------------------------------- #

def test_upsert_and_read_back(store):
    written = store.upsert_many([_rec("NVDA", "2026-05-20", 1.77, 1.87),
                                 _rec("NVDA", "2026-08-26", 2.08, None)])
    assert written == 2
    assert store.count() == 2
    assert store.tickers() == ["NVDA"]


def test_upcoming_becomes_reported_in_place(store):
    """The key is (ticker, date), so the actual lands on the existing row."""
    store.upsert_many([_rec("NVDA", "2026-08-26", 2.08, None)])
    store.upsert_many([_rec("NVDA", "2026-08-26", 2.08, 2.15)])
    rows = store.all()
    assert len(rows) == 1, "a second row was created instead of updating"
    assert rows[0]["eps_actual"] == 2.15
    assert rows[0]["status"] == "reported"


def test_status_is_derived_when_absent(store):
    store.upsert_many([{"ticker": "X", "earnings_date": "2026-01-01",
                        "eps_estimate": 1.0, "eps_actual": 1.1}])
    assert store.all()[0]["status"] == "reported"


def test_rows_come_back_sorted(store):
    store.upsert_many([_rec("NVDA", "2026-08-26", 2.08, None),
                       _rec("AVGO", "2026-09-02", 3.24, None),
                       _rec("NVDA", "2026-05-20", 1.77, 1.87)])
    assert [(r["ticker"], r["earnings_date"]) for r in store.all()] == [
        ("AVGO", "2026-09-02"), ("NVDA", "2026-05-20"), ("NVDA", "2026-08-26")]


def test_meta_roundtrip(store):
    assert store.get_meta("last_refresh") is None
    store.set_meta("last_refresh", "2026-08-13")
    assert store.get_meta("last_refresh") == "2026-08-13"


def test_empty_store_is_not_an_error(store):
    assert store.all() == []
    assert store.tickers() == []
    assert store.count() == 0


# --------------------------------------------------------------------------- #
# export — the contract allocation-gym-2.0 reads
# --------------------------------------------------------------------------- #

def test_export_matches_the_reader_contract(store, tmp_path):
    store.upsert_many([_rec("NVDA", "2026-05-20", 1.77, 1.87),
                       _rec("NVDA", "2026-08-26", 2.08, None)])
    path = store.export_json(str(tmp_path / "eps.json"))

    with open(path) as f:
        payload = json.load(f)

    assert set(payload) == {"generated", "tickers", "eps"}
    assert payload["tickers"] == ["NVDA"]
    assert set(payload["eps"][0]) == {"ticker", "earnings_date", "eps_estimate",
                                      "eps_actual", "status"}
    upcoming = [r for r in payload["eps"] if r["status"] == "upcoming"]
    assert upcoming[0]["eps_actual"] is None


def test_export_of_an_empty_store_is_still_valid(store, tmp_path):
    path = store.export_json(str(tmp_path / "eps.json"))
    with open(path) as f:
        payload = json.load(f)
    assert payload["eps"] == [] and payload["tickers"] == []


# --------------------------------------------------------------------------- #
# status classification — a null actual alone is ambiguous
# --------------------------------------------------------------------------- #

def test_classify_reported_when_an_actual_exists():
    assert classify("2026-05-20", 1.87, "2026-08-13") == "reported"


def test_classify_upcoming_for_a_future_quarter():
    assert classify("2026-08-26", None, "2026-08-13") == "upcoming"


def test_classify_missing_for_an_old_quarter_with_no_figure():
    """Yahoo returns 2021 quarters it never got an actual for.

    Calling those "upcoming" made next_report() answer with a 2021 date.
    """
    assert classify("2021-05-26", None, "2026-08-13") == "missing"


def test_classify_treats_today_as_upcoming():
    assert classify("2026-08-13", None, "2026-08-13") == "upcoming"


def test_trim_drops_missing_quarters():
    records = [
        _rec("P", "2025-01-01", 1.0, 1.1),
        {"ticker": "P", "earnings_date": "2021-05-26", "eps_estimate": -0.06,
         "eps_actual": None, "status": "missing"},
        {"ticker": "P", "earnings_date": "2026-08-26", "eps_estimate": 0.58,
         "eps_actual": None, "status": "upcoming"},
    ]
    kept = trim(records, history_quarters=4)
    assert {r["status"] for r in kept} == {"reported", "upcoming"}
    assert all(r["earnings_date"] != "2021-05-26" for r in kept)
