"""Executor resolves a long_call recipe off a chain snapshot.

Quotes never ride the bus. The intent is identity + recipe; this module
fills strike, expiry, and a bid-side limit after re-read.
"""

from app.pubsub.option_pricer import resolve_long_call


# Friday 16 Oct 2026 closes + Nov 20 last/OI/vol (ChartExchange).
MU_CHAIN = {
    "symbol": "MU",
    "spot": 878.62,
    "as_of": "2026-10-16",
    "expiry": "2026-11-20",
    "dte": 33,
    "calls": [
        {"k": 1050, "last": 21.12, "oi": 8494, "vol": 1548},
        {"k": 1100, "last": 13.25, "oi": 5216, "vol": 3091},
        {"k": 1150, "last": 8.38, "oi": 9110, "vol": 7637},
        {"k": 1200, "last": 5.41, "oi": 4096, "vol": 1252},
    ],
}
AVGO_CHAIN = {
    "symbol": "AVGO",
    "spot": 328.73,
    "as_of": "2026-10-16",
    "expiry": "2026-11-20",
    "dte": 33,
    "calls": [
        {"k": 370, "last": 7.80, "oi": 7069, "vol": 1577},
        {"k": 380, "last": 5.64, "oi": 10432, "vol": 3641},
        {"k": 390, "last": 4.03, "oi": 10211, "vol": 1059},
        {"k": 400, "last": 2.82, "oi": 19492, "vol": 5753},
        {"k": 420, "last": 1.46, "oi": 17528, "vol": 6402},
    ],
}
NET_CHAIN = {
    "symbol": "NET",
    "spot": 296.37,
    "as_of": "2026-10-16",
    "expiry": "2026-11-20",
    "dte": 33,
    "calls": [
        {"k": 360, "last": 11.45, "oi": 1391, "vol": 19},
        {"k": 370, "last": 11.00, "oi": 2192, "vol": 623},
        {"k": 380, "last": 7.60, "oi": 377, "vol": 3},
        {"k": 400, "last": 6.10, "oi": 3102, "vol": 36},
    ],
}

RECIPE = {
    "right": "call",
    "structure": "long_call",
    "dte_rule": {"min": 21, "max": 35},
    "strike_rule": {"kind": "delta", "delta": 0.22},
    "contracts": 1,
    "time_in_force": "day",
}


def test_mu_picks_1050_not_the_cheap_1150():
    ticket = resolve_long_call(MU_CHAIN, RECIPE)
    assert ticket["symbol"] == "MU"
    assert ticket["expiry"] == "2026-11-20"
    assert ticket["strike"] == 1050
    assert ticket["contracts"] == 1
    assert ticket["limit"] < 21.12
    assert ticket["limit"] >= 20.0


def test_avgo_picks_380_not_400():
    ticket = resolve_long_call(AVGO_CHAIN, RECIPE)
    assert ticket["strike"] == 380
    assert ticket["limit"] < 5.64
    assert ticket["limit"] >= 5.40


def test_net_skips_380_dead_book():
    ticket = resolve_long_call(NET_CHAIN, RECIPE)
    assert ticket["strike"] == 370
    assert ticket["limit"] < 11.00


def test_wide_open_or_wrong_tenor_returns_none():
    dead = dict(MU_CHAIN, dte=2)
    assert resolve_long_call(dead, RECIPE) is None
