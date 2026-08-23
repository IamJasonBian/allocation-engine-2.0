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


def test_position_series_variance_and_monthly_growth():
    from statistics import variance
    from app.pnl import position_series
    mark = [
        {"date": "2026-01-30", "close": 100.0, "position": 2.0},
        {"date": "2026-01-31", "close": 110.0, "position": 2.0},
        {"date": "2026-02-01", "close": 105.0, "position": 2.0},
        {"date": "2026-02-02", "close": 115.0, "position": 2.0},
    ]
    out = position_series(mark)
    usd = [200.0, 220.0, 210.0, 230.0]
    rets = [usd[i] / usd[i - 1] - 1 for i in range(1, 4)]
    daily = out["daily"]
    assert [p["date"] for p in daily] == [p["date"] for p in mark]
    assert [p["position"] for p in daily] == usd
    assert daily[0]["positionReturn"] is None
    assert [p["positionReturn"] for p in daily[1:]] == pytest.approx(rets, abs=1e-9)
    assert out["variance"] == pytest.approx(variance(rets), abs=1e-9)
    months = {m["month"]: m["growthRatePct"] for m in out["monthlyGrowth"]}
    assert months["2026-01"] == pytest.approx((220 / 200 - 1) * 100, abs=0.001)
    assert months["2026-02"] == pytest.approx((230 / 210 - 1) * 100, abs=0.001)


def test_ticker_risk_model_std_and_standardized_growth():
    from statistics import mean, stdev
    from app.pnl import ticker_risk_model
    out = ticker_risk_model([100.0, 110.0, 105.0, 115.0])
    gr = [110 / 100 - 1, 105 / 110 - 1, 115 / 105 - 1]
    mu, sig = mean(gr), stdev(gr)
    z = [(g - mu) / sig for g in gr]
    assert out["closeStdUsd"] == pytest.approx(stdev([100.0, 110.0, 105.0, 115.0]), abs=0.01)
    assert out["growthRatePct"] == pytest.approx(mu * 100, abs=0.001)
    assert out["growthRateMeanPct"] == pytest.approx(mu * 100, abs=0.001)
    assert out["growthRateStdPct"] == pytest.approx(sig * 100, abs=0.001)
    assert out["riskAdjustedGrowthRate"] == pytest.approx(mu / sig, abs=0.001)
    assert out["growthRateZ"] == pytest.approx(z[-1], abs=0.001)
    assert out["growthRateZSeries"] == pytest.approx(z, abs=0.001)


def test_pnl_risk_ticker_close_and_growth_rate_std():
    # Long 2 units from day 1 at 100; closes 100, 110, 105, 115.
    from statistics import stdev
    from app.pnl import pnl_risk
    fills = [_fill("BTC", "BUY", 2, 100.0, 10)]
    closes = [
        {"date": (NOW - timedelta(days=10 - i)).date().isoformat(), "close": c}
        for i, c in enumerate([100.0, 110.0, 105.0, 115.0])
    ]
    out = pnl_risk(fills, closes, "BTC")
    assert out["method"] == "additive_ticker_vol"
    assert out["observations"] == 4
    assert out["position"] == 2
    assert [p["totalPnl"] for p in out["series"]] == [0.0, 20.0, 10.0, 30.0]
    close_std = stdev([100.0, 110.0, 105.0, 115.0])
    assert out["closeStdUsd"] == pytest.approx(close_std, abs=0.01)
    gr = [110 / 100 - 1, 105 / 110 - 1, 115 / 105 - 1]
    gr_std = stdev(gr)
    assert out["growthRateStdPct"] == pytest.approx(gr_std * 100, abs=0.001)
    # 1σ daily USD: |position| × last close × stdev(growth rates)
    assert out["riskUsd"] == pytest.approx(2 * 115.0 * gr_std, abs=0.01)


def test_format_ticker_risk_telegram_text():
    from app.pnl import format_ticker_risk, pnl_risk
    fills = [_fill("BTC", "BUY", 2, 100.0, 10)]
    closes = [
        {"date": (NOW - timedelta(days=10 - i)).date().isoformat(), "close": c}
        for i, c in enumerate([100.0, 110.0, 105.0, 115.0])
    ]
    risk = pnl_risk(fills, closes, "BTC")
    text = format_ticker_risk("BTC", risk)
    assert text == (
        f"BTC risk\n"
        f"pos 2.0\n"
        f"σ ${risk['riskUsd']}\n"
        f"close σ ${risk['closeStdUsd']}\n"
        f"GR {risk['growthRatePct']}%\n"
        f"RA GR {risk['riskAdjustedGrowthRate']}\n"
        f"GR σ {risk['growthRateStdPct']}%\n"
        f"var {risk['variance']:.6f}"
    )


def test_today_walk_backs_out_todays_fills_from_live_book():
    from datetime import date
    from app.pnl import today_walk
    day = date(2026, 8, 21)
    book = [
        {"symbol": "CRDO", "quantity": 22.0, "current_price": 216.41},
        {"symbol": "NBIS", "quantity": 89.01, "current_price": 190.49},
        {"symbol": "BTC.SHADOW", "quantity": 498.0, "current_price": 34.22},
    ]
    fills = [
        {"symbol": "CRDO", "side": "BUY", "qty": 10.0, "price": 225.0,
         "ts": datetime(2026, 8, 21, 14, 20, tzinfo=timezone.utc)},
        {"symbol": "NBIS", "side": "BUY", "qty": 12.0, "price": 225.0,
         "ts": datetime(2026, 8, 20, 8, 30, tzinfo=timezone.utc)},
    ]
    out = today_walk(book, fills, day)
    crdo = out["CRDO"]
    assert crdo["nowQty"] == pytest.approx(22.0)
    assert crdo["sodQty"] == pytest.approx(12.0)
    assert crdo["position"] == pytest.approx(22.0 * 216.41)
    assert crdo["fills"] == 1
    nbis = out["NBIS"]
    assert nbis["nowQty"] == pytest.approx(89.01)
    assert nbis["sodQty"] == pytest.approx(89.01)
    assert nbis["fills"] == 0
    assert "BTC.SHADOW" not in out


def test_risk_from_today_walk_scales_live_qty():
    from statistics import stdev
    from app.pnl import risk_from_today_walk
    closes = [100.0, 110.0, 105.0, 115.0]
    walk = {"nowQty": 22.0, "last": 115.0, "date": "2026-08-21"}
    dates = ["2026-08-18", "2026-08-19", "2026-08-20", "2026-08-21"]
    out = risk_from_today_walk(walk, closes, dates=dates)
    gr = [110 / 100 - 1, 105 / 110 - 1, 115 / 105 - 1]
    assert out["position"] == 22.0
    assert out["riskUsd"] == pytest.approx(22 * 115.0 * stdev(gr), abs=0.01)
    assert out["closeStdUsd"] == pytest.approx(stdev(closes), abs=0.01)
