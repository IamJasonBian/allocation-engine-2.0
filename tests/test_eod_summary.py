"""EOD pending option orders summary."""

from datetime import datetime
from zoneinfo import ZoneInfo

from app.risk.eod_summary import build_eod_pending_message, maybe_eod_pending_summary

ET = ZoneInfo("America/New_York")

AMD_SPREAD = {
    "state": "queued",
    "quantity": 1,
    "limit_price": 12.8,
    "direction": "debit",
    "opening_strategy": "long_call_spread",
    "legs": [
        {"chain_symbol": "AMD", "strike": 470, "expiration_date": "2026-11-04",
         "option_type": "call"},
        {"chain_symbol": "AMD", "strike": 510, "expiration_date": "2026-11-04",
         "option_type": "call"},
    ],
}


def test_lists_pending_spread():
    msg = build_eod_pending_message(
        option_orders=[AMD_SPREAD],
        as_of=datetime(2026, 11, 3, tzinfo=ET).date(),
    )
    assert "470/510" in msg and "long_call_spread" in msg and "queued" in msg


def test_always_fires_with_none():
    msg = build_eod_pending_message(option_orders=[], as_of=datetime(2026, 11, 3, tzinfo=ET).date())
    assert "EOD pending options" in msg and "• (none)" in msg


def test_once_per_day_after_eod_time():
    sent: set[str] = set()
    at = datetime(2026, 11, 3, 16, 10, tzinfo=ET)
    msg1, sent = maybe_eod_pending_summary([AMD_SPREAD], sent, now=at)
    msg2, _ = maybe_eod_pending_summary([AMD_SPREAD], sent, now=at)
    assert msg1 and msg2 is None
