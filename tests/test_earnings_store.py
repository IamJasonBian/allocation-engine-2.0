"""Tests for the EPS storage interface and the write path. No network."""

import json

import pytest

from app.earnings_store import EarningsStore
from app.earnings_writer import classify, trim


@pytest.fixture
def store(tmp_path):
    s = EarningsStore(str(tmp_path / "eps.sqlite3"))
    yield s
    s.close()


def _rec(ticker, date, est, actual, status=None):
    return {"ticker": ticker, "earnings_date": date, "eps_estimate": est,
            "eps_actual": actual,
            "status": status or ("reported" if actual is not None else "upcoming")}


@pytest.mark.parametrize("date,actual,expected", [
    ("2026-05-20", 1.87, "reported"),
    ("2026-08-26", None, "upcoming"),
    ("2026-08-13", None, "upcoming"),   # today counts as still to come
    # Yahoo returns old quarters it never got a figure for. Calling those
    # "upcoming" made the next-report lookup answer with a 2021 date.
    ("2021-05-26", None, "missing"),
])
def test_classify_needs_the_date_not_just_a_null_actual(date, actual, expected):
    assert classify(date, actual, "2026-08-13") == expected


def test_trim_keeps_four_reported_quarters_and_every_upcoming_one():
    records = [_rec("X", f"2025-{m:02d}-01", 1.0, 1.1) for m in range(1, 9)]
    records.append(_rec("X", "2026-09-02", 3.24, None))
    kept = trim(records, history_quarters=4)

    reported = [r for r in kept if r["status"] == "reported"]
    assert [r["earnings_date"] for r in reported] == [
        "2025-05-01", "2025-06-01", "2025-07-01", "2025-08-01"]
    # Both sides of each historical quarter survive — that pairing is the point.
    assert (reported[0]["eps_estimate"], reported[0]["eps_actual"]) == (1.0, 1.1)
    assert [r["eps_estimate"] for r in kept if r["status"] == "upcoming"] == [3.24]


def test_trim_drops_missing_quarters_and_windows_per_ticker():
    records = [
        _rec("A", "2025-01-01", 1.0, 1.1),
        _rec("A", "2021-05-26", -0.06, None, status="missing"),
        _rec("B", "2025-01-01", 2.0, 2.1),
    ]
    kept = trim(records, history_quarters=4)
    assert {r["status"] for r in kept} == {"reported"}
    assert sorted(r["ticker"] for r in kept) == ["A", "B"]


def test_upcoming_becomes_reported_in_place(store):
    """Keyed on (ticker, date), so the actual lands on the existing row."""
    store.upsert_many([_rec("NVDA", "2026-08-26", 2.08, None)])
    store.upsert_many([_rec("NVDA", "2026-08-26", 2.08, 2.15)])

    rows = store.all()
    assert len(rows) == 1, "a second row was created instead of updating"
    assert (rows[0]["eps_actual"], rows[0]["status"]) == (2.15, "reported")


def test_rows_come_back_sorted_by_ticker_then_date(store):
    store.upsert_many([_rec("NVDA", "2026-08-26", 2.08, None),
                       _rec("AVGO", "2026-09-02", 3.24, None),
                       _rec("NVDA", "2026-05-20", 1.77, 1.87)])
    assert [(r["ticker"], r["earnings_date"]) for r in store.all()] == [
        ("AVGO", "2026-09-02"), ("NVDA", "2026-05-20"), ("NVDA", "2026-08-26")]


def test_export_matches_the_contract_gym_reads(store, tmp_path):
    store.upsert_many([_rec("NVDA", "2026-05-20", 1.77, 1.87),
                       _rec("NVDA", "2026-08-26", 2.08, None)])
    with open(store.export_json(str(tmp_path / "eps.json"))) as f:
        payload = json.load(f)

    assert set(payload) == {"generated", "tickers", "eps"}
    assert payload["tickers"] == ["NVDA"]
    assert set(payload["eps"][0]) == {"ticker", "earnings_date", "eps_estimate",
                                      "eps_actual", "status"}


def test_empty_store_exports_a_valid_empty_dataset(store, tmp_path):
    assert store.all() == [] and store.count() == 0
    with open(store.export_json(str(tmp_path / "eps.json"))) as f:
        assert json.load(f)["eps"] == []
