"""EOD trading-day summary scheduling and formatting."""

from datetime import datetime
from zoneinfo import ZoneInfo

from app.risk.eod_summary import build_eod_message, maybe_eod_summary

ET = ZoneInfo("America/New_York")

AMD_SPREAD = {
    "state": "queued",
    "quantity": 1,
    "limit_price": 12.8,
    "direction": "debit",
    "opening_strategy": "long_call_spread",
    "legs": [
        {"chain_symbol": "AMD", "strike": 470, "expiration_date": "2026-11-04",
         "side": "buy", "option_type": "call"},
        {"chain_symbol": "AMD", "strike": 510, "expiration_date": "2026-11-04",
         "side": "sell", "option_type": "call"},
    ],
}

NBIS = {
    "chain_symbol": "NBIS", "option_type": "call", "strike": 230,
    "expiration": "2026-11-20", "quantity": 31,
    "purchase_price": 2.08, "mark_price": 0.95,
}


def test_build_shows_spread_and_positions():
    msg = build_eod_message(
        option_positions=[NBIS],
        open_equity_orders=[{"symbol": "SPY", "side": "sell", "state": "queued",
                             "quantity": 1, "stop_price": 600}],
        open_option_orders=[AMD_SPREAD],
        as_of=datetime(2026, 11, 3, tzinfo=ET).date(),
    )
    assert "AMD" in msg and "470/510" in msg and "long_call_spread" in msg
    assert "NBIS" in msg and "Pending equity orders (1)" in msg


def test_fires_once_after_eod_time():
    sent: set[str] = set()
    at = datetime(2026, 11, 3, 16, 10, tzinfo=ET)
    msg1, sent = maybe_eod_summary([], [], [AMD_SPREAD], sent, now=at)
    msg2, sent = maybe_eod_summary([], [], [AMD_SPREAD], sent, now=at)
    assert msg1 and "EOD 2026-11-03" in msg1
    assert msg2 is None


def test_skips_before_eod_and_weekends():
    sent: set[str] = set()
    early, sent = maybe_eod_summary([], [], [], sent,
                                  now=datetime(2026, 11, 3, 10, 0, tzinfo=ET))
    assert early is None
    sat, sent = maybe_eod_summary([], [], [], sent,
                                  now=datetime(2026, 11, 7, 17, 0, tzinfo=ET))
    assert sat is None
