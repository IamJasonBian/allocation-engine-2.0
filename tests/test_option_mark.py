"""Mark models — NBIS Nov-20 $230C at overnight spot."""

from app.pricing.mark_models import mark_call_at_spot, run_pricer_backtest

NBIS = {
    "2026-09-22": 236.12,
    "2026-09-23": 226.61,
    "2026-09-24": 243.48,
    "2026-09-25": 237.33,
    "2026-09-28": 231.88,
    "2026-09-29": 237.35,
    "2026-09-30": 235.88,
    "2026-10-01": 232.28,
    "2026-10-02": 242.81,
    "2026-10-05": 232.57,
    "2026-10-06": 249.87,
    "2026-10-07": 237.15,
    "2026-10-08": 219.71,
    "2026-10-09": 221.07,
    "2026-10-12": 210.06,
    "2026-10-13": 206.86,
    "2026-10-14": 186.01,
    "2026-10-15": 188.41,
    "2026-10-16": 165.47,
    "2026-10-19": 170.24,
    "2026-10-20": 171.45,
}
NBIS_230C = {
    "2026-09-22": 36.95,
    "2026-09-23": 31.95,
    "2026-09-24": 40.80,
    "2026-09-25": 35.32,
    "2026-09-28": 31.76,
    "2026-09-29": 35.65,
    "2026-09-30": 33.73,
    "2026-10-01": 30.42,
    "2026-10-02": 36.00,
    "2026-10-05": 27.97,
    "2026-10-06": 38.60,
    "2026-10-07": 29.95,
    "2026-10-08": 20.54,
    "2026-10-09": 19.50,
    "2026-10-12": 14.00,
    "2026-10-13": 13.34,
    "2026-10-14": 7.35,
    "2026-10-15": 7.88,
    "2026-10-16": 3.65,
    "2026-10-19": 3.85,
    "2026-10-20": 3.80,
}


def test_nbis_230c_overnight_spot_164_prev_iv_mark():
    mark = mark_call_at_spot(
        164.0,
        strike=230.0,
        expiry="2026-11-20",
        as_of="2026-10-21",
        underlying=NBIS,
        option=NBIS_230C,
        start="2026-09-29",
    )
    assert mark["prev_day"] == "2026-10-20"
    assert mark["dte"] == 30
    assert mark["marks"]["prev_iv"] == 2.51
    assert mark["marks"]["entry_iv"] == 2.19
    assert mark["marks"]["realized"] == 1.75
    assert mark["primary"] == mark["marks"]["prev_iv"]


def test_backtest_prev_iv_tracks_within_a_day_of_actual():
    rows = run_pricer_backtest(
        NBIS, NBIS_230C, 230.0, "2026-11-20", start="2026-09-29",
    )
    last = rows[-1]
    assert last["date"] == "2026-10-20"
    assert last["actual"] == 3.80
    assert last["prev_iv"] is not None
    assert abs(last["prev_iv"] - last["actual"]) < 0.35
