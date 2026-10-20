"""Proxy mappings: default_overrides (always on) and custom_overrides (windows)."""

from datetime import datetime, timedelta, timezone

from app.overrides import (
    CUSTOM_OVERRIDES,
    DEFAULT_OVERRIDES,
    ENGINE_EVENT_TICKERS,
    CustomOverride,
    resolve_proxy,
)


def test_default_overrides_include_btc_crypto_proxy():
    assert any(
        row.symbol == "BTC" and row.proxy == "BTC/USD"
        for row in DEFAULT_OVERRIDES
    )


def test_resolve_proxy_uses_default_when_no_custom_window():
    assert resolve_proxy("btc") == "BTC/USD"
    assert resolve_proxy("AAPL") is None


def test_engine_event_tickers_have_standing_and_earnings_overrides():
    defaults = {row.symbol: row.proxy for row in DEFAULT_OVERRIDES}
    customs = {row.symbol: row for row in CUSTOM_OVERRIDES}
    assert set(ENGINE_EVENT_TICKERS) == {
        "NBIS", "MU", "NVDA", "QQQ", "NET", "SPY",
    }
    for symbol in ENGINE_EVENT_TICKERS:
        assert symbol in defaults
        row = customs[symbol]
        assert row.start_time < row.end_time
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
