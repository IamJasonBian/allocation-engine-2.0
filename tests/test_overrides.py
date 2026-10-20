"""Proxy mappings: default_overrides (always on) and custom_overrides (windows)."""

from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from app.overrides import (
    CUSTOM_OVERRIDES,
    DEFAULT_OVERRIDES,
    ENGINE_EVENT_TICKERS,
    CustomOverride,
    normalize_timestamp,
    resolve_proxy,
)

_ET = ZoneInfo("America/New_York")


def test_default_overrides_include_btc_crypto_proxy():
    assert any(
        row.symbol == "BTC" and row.proxy == "BTC/USD"
        for row in DEFAULT_OVERRIDES
    )


def test_resolve_proxy_uses_default_when_no_custom_window():
    assert resolve_proxy("btc") == "BTC/USD"
    assert resolve_proxy("AAPL") is None


def test_engine_event_tickers_have_standing_and_two_earnings_windows():
    defaults = {row.symbol: row.proxy for row in DEFAULT_OVERRIDES}
    assert set(ENGINE_EVENT_TICKERS) == {
        "NBIS", "MU", "NVDA", "QQQ", "NET", "SPY",
    }
    for symbol in ENGINE_EVENT_TICKERS:
        assert symbol in defaults
        rows = [r for r in CUSTOM_OVERRIDES if r.symbol == symbol]
        assert len(rows) == 2
        assert rows[0].end_time <= rows[1].start_time
        for row in rows:
            assert row.start_time < row.end_time
            assert row.start_time.tzinfo == timezone.utc
            assert row.end_time.tzinfo == timezone.utc
            assert row.proxy


def test_net_earnings_window_uses_qqq():
    inside = datetime(2026, 11, 5, 22, 0, tzinfo=timezone.utc)
    outside = datetime(2026, 11, 5, 20, 0, tzinfo=timezone.utc)
    assert resolve_proxy("NET", at=inside) == "QQQ"
    assert resolve_proxy("NET", at=outside) == "QQQ"  # standing default


def test_custom_override_wins_inside_its_window(monkeypatch):
    start = datetime(2026, 10, 20, 12, 0, tzinfo=timezone.utc)
    end = start + timedelta(hours=4)
    monkeypatch.setattr(
        "app.overrides.CUSTOM_OVERRIDES",
        (CustomOverride(
            symbol="BTC", proxy="ETH/USD",
            start_time=start, end_time=end,
        ),),
    )
    assert resolve_proxy("BTC", at=start + timedelta(hours=1)) == "ETH/USD"
    assert resolve_proxy("BTC", at=start - timedelta(minutes=1)) == "BTC/USD"
    assert resolve_proxy("BTC", at=end) == "BTC/USD"


def test_normalize_timestamp_utc_naive_et_and_iso():
    utc = datetime(2026, 11, 5, 21, 0, tzinfo=timezone.utc)
    assert normalize_timestamp(utc) == utc
    assert normalize_timestamp(datetime(2026, 11, 5, 21, 0)) == utc
    assert normalize_timestamp("2026-11-05T21:00:00Z") == utc
    et = datetime(2026, 11, 5, 16, 0, tzinfo=_ET)
    assert normalize_timestamp(et) == utc


def test_custom_override_and_resolve_normalize_mixed_inputs():
    row = CustomOverride(
        "BTC", "ETH/USD",
        datetime(2026, 11, 5, 16, 0, tzinfo=_ET),
        "2026-11-06T21:00:00Z",
    )
    assert row.start_time.tzinfo == timezone.utc
    assert row.end_time == datetime(2026, 11, 6, 21, 0, tzinfo=timezone.utc)
    naive_inside = datetime(2026, 11, 5, 22, 0)
    assert resolve_proxy("NET", at=naive_inside) == "QQQ"
