"""Daily pending option summaries (SOD, EOD, startup)."""

from datetime import datetime
from zoneinfo import ZoneInfo

from app.risk.eod_summary import build_pending_message, pending_summaries_due

ET = ZoneInfo("America/New_York")

AMD_SPREAD = {
    "state": "queued",
    "quantity": 1,
    "limit_price": 12.8,
    "direction": "debit",
    "opening_strategy": "long_call_spread",
    "legs": [
        {"chain_symbol": "AMD", "strike": 470, "expiration_date": "2026-11-04"},
        {"chain_symbol": "AMD", "strike": 510, "expiration_date": "2026-11-04"},
    ],
}


def test_sod_and_eod_both_fire_when_due():
    sent: set[str] = set()
    at = datetime(2026, 11, 3, 17, 0, tzinfo=ET)
    msgs, sent = pending_summaries_due([AMD_SPREAD], sent, now=at)
    assert len(msgs) == 2
    assert "SOD pending" in msgs[0] and "EOD pending" in msgs[1]


def test_startup_ping_once():
    sent: set[str] = set()
    at = datetime(2026, 11, 3, 8, 0, tzinfo=ET)
    msgs, sent = pending_summaries_due([], sent, now=at, include_startup=True)
    assert len(msgs) == 1 and "Startup pending" in msgs[0]
    msgs2, _ = pending_summaries_due([], sent, now=at, include_startup=True)
    assert msgs2 == []


def test_empty_pending_still_sends():
    msg = build_pending_message(option_orders=[], label="EOD", as_of=datetime(2026, 11, 3, tzinfo=ET).date())
    assert "• (none)" in msg
