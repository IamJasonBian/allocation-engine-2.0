"""Hand-checkable cases for the metrics & risk library (app/risk)."""

from datetime import datetime, timedelta, timezone
from math import sqrt
from statistics import NormalDist, stdev

import pytest

from app.risk.covariance import covariance_risk
from app.risk.curve import aligned_returns, current_book, per_symbol_marks, portfolio_curve
from app.risk.exposure import exposure
from app.risk.metrics import drawdowns, performance
from app.risk.report import build_report
from app.risk.stress import stress_scenarios
from app.risk.tail import historical_var, parametric_var

NOW = datetime(2026, 8, 20, tzinfo=timezone.utc)


def _d(i):
    return (NOW - timedelta(days=10 - i)).date().isoformat()


def _fill(sym, side, qty, price, day):
    return {"symbol": sym, "side": side, "qty": qty, "price": price, "ts": NOW - timedelta(days=10 - day)}


def _closes(vals, start=0):
    return [{"date": _d(start + i), "close": c} for i, c in enumerate(vals)]


# --- curve -----------------------------------------------------------------

def test_portfolio_curve_sums_and_carries_forward():
    # A long 1 @100: closes 100,110,105.  B short 2 @50: closes 50,45 (no close day 2).
    fills = [_fill("A", "BUY", 1, 100.0, 0), _fill("B", "SELL", 2, 50.0, 0)]
    marks = per_symbol_marks(fills, {"A": _closes([100.0, 110.0, 105.0]), "B": _closes([50.0, 45.0])})
    curve = portfolio_curve(marks)
    assert [p["totalPnl"] for p in curve] == [0.0, 20.0, 15.0]     # B carried at 45 on day 2
    assert [p["grossExposure"] for p in curve] == [200.0, 200.0, 195.0]
    assert [p["netExposure"] for p in curve] == [0.0, 20.0, 15.0]
    assert [p["dailyPnl"] for p in curve] == [0.0, 20.0, -5.0]
    assert curve[1]["returnOnExposure"] == pytest.approx(20 / 200)


def test_aligned_returns_inner_joins_dates():
    fills = [_fill("A", "BUY", 1, 1.0, 0), _fill("B", "BUY", 1, 1.0, 0)]
    marks = per_symbol_marks(fills, {"A": _closes([1.0, 2.0, 4.0]), "B": _closes([1.0, 3.0], start=1)})
    dates, rets = aligned_returns(marks)
    assert dates == [_d(1), _d(2)]
    assert rets["A"] == [pytest.approx(1.0)]
    assert rets["B"] == [pytest.approx(2.0)]


# --- metrics ---------------------------------------------------------------

def test_drawdowns_peak_to_trough():
    curve = [{"date": _d(i), "totalPnl": v, "grossExposure": 1000.0}
             for i, v in enumerate([0, 100, 50, -20, 30, 120, 90])]
    dd = drawdowns(curve)
    assert dd["maxDrawdownUsd"] == -120.0           # 100 -> -20
    assert dd["maxDrawdownPct"] == pytest.approx(-12.0)
    assert dd["drawdownStart"] == _d(1) and dd["drawdownTrough"] == _d(3)
    assert dd["inDrawdown"] and dd["currentDrawdownUsd"] == -30.0
    assert dd["longestDrawdownDays"] == 4            # d1 -> d5 recovery


def test_performance_sharpe_and_hit_rate():
    rets = [0.01, -0.005, 0.02, 0.0, -0.01, 0.015]
    curve = [{"date": _d(0), "totalPnl": 0.0, "grossExposure": 1000.0, "dailyPnl": 0.0, "returnOnExposure": 0.0}]
    pnl = 0.0
    for i, x in enumerate(rets, 1):
        pnl += x * 1000
        curve.append({"date": _d(i), "totalPnl": pnl, "grossExposure": 1000.0,
                      "dailyPnl": x * 1000, "returnOnExposure": x})
    perf = performance(curve)
    active = [x for x in rets if x != 0.0]           # flat day excluded
    mu, sd = sum(active) / len(active), stdev(active)
    assert perf["observations"] == 5
    assert perf["periodsPerYear"] == 365              # consecutive days
    assert perf["sharpe"] == pytest.approx(mu / sd * sqrt(365), abs=1e-3)
    assert perf["hitRatePct"] == pytest.approx(60.0)
    assert perf["profitFactor"] == pytest.approx(45 / 15, abs=1e-3)
    assert perf["worstDayUsd"] == -10.0


# --- exposure --------------------------------------------------------------

def test_exposure_concentration():
    book = {"A": {"position": 1, "lastClose": 600.0, "notional": 600.0},
            "B": {"position": -2, "lastClose": 200.0, "notional": -400.0},
            "C": {"position": 0, "lastClose": 10.0, "notional": 0.0}}
    e = exposure(book)
    assert e["openPositions"] == 2
    assert e["grossExposureUsd"] == 1000.0 and e["netExposureUsd"] == 200.0
    assert e["hhi"] == pytest.approx(0.6 ** 2 + 0.4 ** 2)
    assert e["effectiveN"] == pytest.approx(1 / 0.52, abs=0.01)
    assert e["largestPosition"] == "A" and e["largestPositionPct"] == 60.0


# --- covariance ------------------------------------------------------------

def test_covariance_perfect_correlation_equals_additive():
    rets = {"A": [0.01, -0.02, 0.03, -0.01], "B": [0.02, -0.04, 0.06, -0.02]}  # B = 2A
    book = {"A": {"notional": 1000.0}, "B": {"notional": 500.0}}
    c = covariance_risk([_d(i) for i in range(5)], rets, book)
    assert c["portfolioDailySigmaUsd"] == pytest.approx(c["additiveSigmaUsd"], abs=0.01)
    assert c["diversificationRatio"] == pytest.approx(1.0, abs=1e-3)
    assert c["correlation"]["A"]["B"] == pytest.approx(1.0)
    assert sum(x["componentRiskUsd"] for x in c["contributions"]) == pytest.approx(c["portfolioDailySigmaUsd"], abs=0.02)


def test_covariance_short_hedge_cancels_risk():
    rets = {"A": [0.01, -0.02, 0.03, -0.01], "B": [0.01, -0.02, 0.03, -0.01]}
    book = {"A": {"notional": 1000.0}, "B": {"notional": -1000.0}}
    c = covariance_risk([_d(i) for i in range(5)], rets, book)
    assert c["portfolioDailySigmaUsd"] == pytest.approx(0.0, abs=1e-6)
    assert c["additiveSigmaUsd"] > 0


def test_covariance_negative_correlation_flags_hedge():
    rets = {"A": [0.01, -0.02, 0.03, -0.01], "B": [-0.005, 0.01, -0.015, 0.005]}  # B = -A/2
    book = {"A": {"notional": 1000.0}, "B": {"notional": 500.0}}
    c = covariance_risk([_d(i) for i in range(5)], rets, book)
    b = next(x for x in c["contributions"] if x["symbol"] == "B")
    assert b["isHedge"] and b["componentRiskUsd"] < 0


# --- tail ------------------------------------------------------------------

def test_historical_var_on_current_book():
    rets = {"A": [-0.10, -0.05, 0.0, 0.01, 0.02, 0.03, 0.04, 0.05, 0.06, 0.07, 0.08]}
    book = {"A": {"notional": 1000.0}}
    t = historical_var(rets, book)
    # 5th percentile of 11 pts: pos 0.5 -> between -100 and -50 = -75
    assert t["levels"]["95"]["varUsd"] == pytest.approx(75.0)
    assert t["levels"]["95"]["cvarUsd"] == pytest.approx(100.0)  # only -100 is <= -75
    assert t["worstDayUsd"] == -100.0


def test_parametric_var_is_z_times_sigma():
    p = parametric_var(100.0)
    z = NormalDist()
    assert p["levels"]["95"]["varUsd"] == pytest.approx(z.inv_cdf(0.95) * 100, abs=0.01)
    assert p["levels"]["99"]["cvarUsd"] > p["levels"]["99"]["varUsd"]


# --- stress ----------------------------------------------------------------

def test_stress_uniform_shock_respects_sign():
    book = {"A": {"notional": 1000.0}, "B": {"notional": -500.0}}
    rets = {"A": [0.01, -0.03, 0.02, 0.0, -0.01, 0.01], "B": [0.0, 0.01, -0.02, 0.0, 0.0, 0.0]}
    s = stress_scenarios(rets, [_d(i) for i in range(7)], book)
    by = {sc["name"]: sc for sc in s["scenarios"]}
    assert by["Everything -10%"]["pnlUsd"] == pytest.approx(-50.0)
    assert by["Everything -10%"]["pnlPctOfGross"] == pytest.approx(-50 / 1500 * 100, abs=0.01)
    worst = next(v for k, v in by.items() if k.startswith("Worst observed day"))
    assert worst["pnlUsd"] == pytest.approx(1000 * -0.03 + -500 * 0.01)   # day index 1
    assert any(k.startswith("Worst 5-day window") for k in by)


# --- report ----------------------------------------------------------------

def test_build_report_end_to_end():
    fills = [_fill("A", "BUY", 2, 100.0, 0), _fill("B", "SELL", 1, 50.0, 0)]
    closes = {"A": _closes([100.0, 104.0, 101.0, 106.0, 103.0, 108.0]),
              "B": _closes([50.0, 51.0, 49.0, 52.0, 50.0, 53.0])}
    rep = build_report(fills, closes, now=NOW)
    assert rep["asOf"] == _d(5)
    h = rep["headline"]
    assert h["totalPnlUsd"] == pytest.approx(2 * 8 - 3)
    assert h["openPositions"] == 2
    assert h["grossExposureUsd"] == pytest.approx(2 * 108 + 53)
    assert set(rep) >= {"flags", "performance", "drawdown", "exposure", "covariance", "tail", "stress", "symbols", "curve"}
    assert rep["flags"] and all({"severity", "code", "text"} <= set(f) for f in rep["flags"])
    assert [s["symbol"] for s in rep["symbols"]] == ["A", "B"]
    assert current_book(per_symbol_marks(fills, closes))["B"]["notional"] == -53.0
