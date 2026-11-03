"""EOD trading-day summary scheduling and formatting."""

from datetime import datetime
from zoneinfo import ZoneInfo

from app.risk.eod_summary import (
    build_eod_message,
    maybe_eod_summary,
    open_equity_orders,
    open_option_orders,
)

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


def test_open_filters_use_rh_status_and_qty_fields():
    eq = open_equity_orders([{"symbol": "SPY", "status": "queued", "qty": 2}])
    assert len(eq) == 1 and eq[0]["quantity"] == 2
    opts = open_option_orders([AMD_SPREAD, {"state": "filled", "quantity": 1}])
    assert len(opts) == 1 and opts[0]["opening_strategy"] == "long_call_spread"


def test_build_shows_spread_and_positions():
    msg = build_eod_message(
        option_positions=[NBIS],
        equity_orders=[{"symbol": "SPY", "side": "sell", "status": "queued",
                        "qty": 1, "stop_price": 600}],
        option_orders=[AMD_SPREAD],
        as_of=datetime(2026, 11, 3, tzinfo=ET).date(),
    )
    assert "AMD" in msg and "470/510" in msg and "long_call_spread" in msg
    assert "NBIS" in msg and "Pending equity orders (1)" in msg
    assert "Pending option orders (1)" in msg


def test_build_always_lists_empty_sections():
    msg = build_eod_message(
        option_positions=[], equity_orders=[], option_orders=[],
        as_of=datetime(2026, 11, 3, tzinfo=ET).date(),
    )
    assert "Pending option orders (0)" in msg and "(none)" in msg
    assert "Open option positions (0)" in msg


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
