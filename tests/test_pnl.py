"""PnlSnapshot / replay_fills — average-cost realized P&L."""

from datetime import datetime, timedelta, timezone

import pytest

from app.pnl import PnlSnapshot, compute_pnl, replay_fills

NOW = datetime(2026, 8, 19, tzinfo=timezone.utc)


def _fill(symbol, side, qty, price, days_ago):
    return {
        "symbol": symbol, "side": side, "qty": qty, "price": price,
        "ts": NOW - timedelta(days=days_ago),
    }


def test_msft_example_average_cost():
    # +75 @ 25.10, +50 @ 25.12, -100 @ 25.22
    # avg cost = (75*25.10 + 50*25.12) / 125 = 25.108
    # realized = 100 * (25.22 - 25.108) = 11.20 (avg-cost; FIFO would be 11.50)
    snap = PnlSnapshot("MSFT")
    snap.update_by_tradefeed(1, 25.10, 75)
    snap.update_by_tradefeed(1, 25.12, 50)
    delta = snap.update_by_tradefeed(2, 25.22, 100)
    assert delta == pytest.approx(11.20)
    assert snap.m_realized_pnl == pytest.approx(11.20)
    assert snap.m_net_position == pytest.approx(25)
    assert snap.m_avg_open_price == pytest.approx(25.108)


def test_extending_position_realizes_nothing():
    snap = PnlSnapshot("X")
    assert snap.update_by_tradefeed(1, 10.0, 100) == 0.0
    assert snap.update_by_tradefeed(1, 12.0, 100) == 0.0
    assert snap.m_realized_pnl == 0.0
    assert snap.m_avg_open_price == pytest.approx(11.0)


def test_flip_long_to_short_resets_basis():
    snap = PnlSnapshot("X")
    snap.update_by_tradefeed(1, 100.0, 10)
    delta = snap.update_by_tradefeed(2, 110.0, 15)
    # realizes only the 10 long shares; the extra 5 open a short at 110
    assert delta == pytest.approx(100.0)
    assert snap.m_net_position == pytest.approx(-5)
    assert snap.m_avg_open_price == pytest.approx(110.0)


def test_short_side_realized():
    snap = PnlSnapshot("X")
    snap.update_by_tradefeed(2, 50.0, 10)  # short from flat
    delta = snap.update_by_tradefeed(1, 45.0, 10)  # cover lower
    assert delta == pytest.approx(50.0)
    assert snap.m_net_position == pytest.approx(0)


def test_update_by_marketdata_unrealized():
    snap = PnlSnapshot("X")
    snap.update_by_tradefeed(1, 10.0, 100)
    snap.update_by_marketdata(12.5)
    assert snap.m_unrealized_pnl == pytest.approx(250.0)
    assert snap.m_total_pnl == pytest.approx(250.0)


def test_replay_sorts_out_of_order_fills():
    # Delivered out of order: the sell realizes against the earlier buy only
    fills = [
        _fill("A", "SELL", 5, 20.0, days_ago=1),
        _fill("A", "BUY", 10, 10.0, days_ago=5),
    ]
    snaps, events = replay_fills(fills)
    assert snaps["A"].m_realized_pnl == pytest.approx(50.0)
    assert len(events) == 1
    assert events[0]["realized"] == pytest.approx(50.0)


def test_basis_outside_window_still_correct():
    # Buy long before any reporting window, sell recently: the realize event
    # carries the correct basis and its own timestamp for window filtering.
    fills = [
        _fill("A", "BUY", 100, 10.0, days_ago=90),
        _fill("A", "SELL", 50, 20.0, days_ago=3),
    ]
    _, events = replay_fills(fills)
    cutoff = NOW - timedelta(days=30)
    windowed = sum(e["realized"] for e in events if e["ts"] >= cutoff)
    assert windowed == pytest.approx(500.0)


def test_replay_multiple_symbols_isolated():
    fills = [
        _fill("A", "BUY", 10, 10.0, days_ago=4),
        _fill("B", "BUY", 10, 100.0, days_ago=4),
        _fill("A", "SELL", 10, 11.0, days_ago=2),
    ]
    snaps, events = replay_fills(fills)
    assert snaps["A"].m_realized_pnl == pytest.approx(10.0)
    assert snaps["B"].m_realized_pnl == 0.0
    assert {e["symbol"] for e in events} == {"A"}


def test_compute_pnl_basis_outside_window():
    fills = [
        _fill("A", "BUY", 100, 10.0, days_ago=90),
        _fill("A", "SELL", 50, 20.0, days_ago=3),
    ]
    out = compute_pnl(fills, days=30, now=NOW)
    assert out["totalRealizedPnL"] == pytest.approx(500.0)
    sym = out["symbols"][0]
    assert sym["realizedPnL"] == pytest.approx(500.0)
    assert sym["netPosition"] == pytest.approx(50)
    assert sym["avgOpenPrice"] == pytest.approx(10.0)


def test_compute_pnl_total_with_mark_prices():
    fills = [
        _fill("A", "BUY", 100, 10.0, days_ago=90),
        _fill("A", "SELL", 50, 20.0, days_ago=3),
    ]
    out = compute_pnl(fills, days=30, now=NOW, mark_prices={"A": 15.0})
    assert out["totalRealizedPnL"] == pytest.approx(500.0)
    assert out["totalUnrealizedPnL"] == pytest.approx(250.0)
    assert out["totalPnL"] == pytest.approx(750.0)
    sym = out["symbols"][0]
    assert sym["unrealizedPnL"] == pytest.approx(250.0)
    assert sym["totalPnL"] == pytest.approx(750.0)
    assert sym["markPrice"] == pytest.approx(15.0)


def test_pnl_risk_std_of_daily_marks():
    # Long 1 unit from day 1 at 100; closes 100, 110, 105, 115.
    # Daily PnL: 0, 10, 5, 15 -> deltas [10, -5, 10], stdev = 8.66
    fills = [_fill("BTC", "BUY", 1, 100.0, 10)]
    closes = [
        {"date": (NOW - timedelta(days=10 - i)).date().isoformat(), "close": c}
        for i, c in enumerate([100.0, 110.0, 105.0, 115.0])
    ]
    from app.pnl import pnl_risk
    out = pnl_risk(fills, closes, "BTC")
    assert out["observations"] == 3
    assert [p["totalPnl"] for p in out["series"]] == [0.0, 10.0, 5.0, 15.0]
    assert out["dailyStdUsd"] == pytest.approx(8.66, abs=0.01)
    # pct returns: 10/100, -5/110, 10/105
    from statistics import stdev
    expected_pct = stdev([0.10, -5 / 110, 10 / 105]) * 100
    assert out["dailyStdPct"] == pytest.approx(expected_pct, abs=0.001)
    assert out["periodsPerYear"] == 365  # consecutive calendar days


def test_pnl_risk_flat_days_excluded():
    # Position opens on the 3rd close day; the flat-flat delta is excluded.
    fills = [_fill("BTC", "BUY", 1, 100.0, 8)]
    closes = [
        {"date": (NOW - timedelta(days=10 - i)).date().isoformat(), "close": c}
        for i, c in enumerate([90.0, 95.0, 100.0, 110.0])
    ]
    from app.pnl import pnl_risk
    out = pnl_risk(fills, closes, "BTC")
    # Only day2->day3 has prior exposure: delta 10 on exposure 100
    assert out["observations"] == 1
    assert "dailyStdUsd" not in out  # needs >= 2 observations


def test_portfolio_risk_sums_symbols():
    # A: long 1 @100; closes 100, 110, 105. B: long 2 @50; closes 50, 55, 45.
    # Portfolio PnL: 0, 20, -5 -> deltas [20, -25]
    # Exposure: d0 = 100 + 100 = 200, d1 = 110 + 110 = 220
    from app.pnl import portfolio_risk
    fills = [_fill("AAA", "BUY", 1, 100.0, 10), _fill("BBB", "BUY", 2, 50.0, 10)]
    d = lambda i: (NOW - timedelta(days=10 - i)).date().isoformat()
    closes = {
        "AAA": [{"date": d(i), "close": c} for i, c in enumerate([100.0, 110.0, 105.0])],
        "BBB": [{"date": d(i), "close": c} for i, c in enumerate([50.0, 55.0, 45.0])],
    }
    out = portfolio_risk(fills, closes)
    assert out["symbols"] == ["AAA", "BBB"]
    assert [p["totalPnl"] for p in out["series"]] == [0.0, 20.0, -5.0]
    assert [p["grossExposure"] for p in out["series"]] == [200.0, 220.0, 195.0]
    assert out["observations"] == 2
    from statistics import stdev
    assert out["dailyStdUsd"] == pytest.approx(stdev([20.0, -25.0]), abs=0.01)
    assert out["dailyStdPct"] == pytest.approx(stdev([20 / 200, -25 / 220]) * 100, abs=0.001)


def test_portfolio_risk_forward_fills_missing_close():
    # B has no close on the last day; its last mark carries forward.
    from app.pnl import portfolio_risk
    fills = [_fill("AAA", "BUY", 1, 100.0, 10), _fill("BBB", "BUY", 2, 50.0, 10)]
    d = lambda i: (NOW - timedelta(days=10 - i)).date().isoformat()
    closes = {
        "AAA": [{"date": d(i), "close": c} for i, c in enumerate([100.0, 110.0, 105.0])],
        "BBB": [{"date": d(i), "close": c} for i, c in enumerate([50.0, 55.0])],
    }
    out = portfolio_risk(fills, closes)
    # Last day: A pnl 5 + B carried at 55 (pnl 10) = 15
    assert [p["totalPnl"] for p in out["series"]] == [0.0, 20.0, 15.0]
