"""Tests for the shadow index engine — BTC/USD → Grayscale BTC Mini Trust ETF."""

import pytest

from app.enums import AssetType, RiskEventType
from app.risk.events import RiskEvent
from app.shadow_index import (
    IndexConfig, btc_to_index_price, build_shadow_position,
    check_shadow_drift, check_order_shadow_drift,
)


# ── Fixtures ─────────────────────────────────────────────────────────────────

@pytest.fixture
def config():
    """Standard BTC Mini Trust config: ETF closed at $31.05 when BTC was $70k."""
    return IndexConfig(
        shadow_symbol="BTC.shadow",
        etf_symbol="BTC",
        crypto_symbol="BTC/USD",
        last_close=31.05,
        btc_at_close=70_000,
    )


@pytest.fixture
def config_no_close():
    return IndexConfig(
        shadow_symbol="BTC.shadow",
        etf_symbol="BTC",
        crypto_symbol="BTC/USD",
        last_close=None,
        btc_at_close=None,
    )


def _order(side: str, limit_price: float, symbol: str = "BTC") -> dict:
    return {
        "id": f"ord-{side}-{limit_price}",
        "symbol": symbol,
        "side": side,
        "limit_price": limit_price,
        "type": "limit",
        "status": "confirmed",
        "qty": 10,
    }


# ── btc_to_index_price ──────────────────────────────────────────────────────

class TestBtcToIndexPrice:
    def test_no_change(self, config):
        # BTC unchanged at $70k → ETF stays at $31.05
        price = btc_to_index_price(70_000, config)
        assert round(price, 2) == 31.05

    def test_btc_drops_3pct(self, config):
        # BTC drops 3% from $70k to $67,900 → ETF = 31.05 * (67900/70000) = $30.12
        price = btc_to_index_price(67_900, config)
        assert round(price, 2) == 30.12

    def test_btc_rallies_10pct(self, config):
        # BTC rallies 10% to $77k → ETF = 31.05 * (77000/70000) = $34.16
        price = btc_to_index_price(77_000, config)
        assert round(price, 2) == 34.16

    def test_no_config_returns_zero(self, config_no_close):
        assert btc_to_index_price(70_000, config_no_close) == 0.0

# ── build_shadow_position ───────────────────────────────────────────────────

class TestBuildShadowPosition:
    def test_position_shape(self, config):
        pos = build_shadow_position(70_000, config, qty=100)
        assert pos["symbol"] == "BTC.shadow"
        assert pos["asset_type"] == AssetType.SHADOW_EQUITY
        assert pos["qty"] == 100
        assert pos["current_price"] == pytest.approx(31.05, abs=0.02)
        assert pos["avg_entry"] == 31.05

    def test_no_close_uses_projected_as_entry(self, config_no_close):
        pos = build_shadow_position(70_000, config_no_close, qty=10)
        # No config → projected = 0, entry = 0
        assert pos["current_price"] == 0.0

# ── check_shadow_drift ──────────────────────────────────────────────────────

class TestCheckShadowDrift:
    def test_above_threshold_emits_event(self, config):
        # BTC rallies to $78k → projected $34.59 vs close $31.05 → drift ≈ +11.4%
        event = check_shadow_drift(78_000, config)
        assert event is not None
        assert event.event_type == RiskEventType.PRICE_DEPEG
        assert event.symbol == "BTC.shadow"
        assert event.drift_pct >= 0.08
        assert event.metadata["direction"] == "above"

# ── check_order_shadow_drift ────────────────────────────────────────────────

class TestCheckOrderShadowDrift:
    def test_no_btc_orders_returns_empty(self, config):
        orders = [_order("BUY", 150.0, symbol="AAPL")]
        events = check_order_shadow_drift(70_000, config, orders)
        assert events == []

    def test_buy_gap_up_risk(self, config):
        # BTC rallies to $78k → projected $34.59, limit $31.00 → drift ≈ +11.6%
        orders = [_order("BUY", 31.00)]
        events = check_order_shadow_drift(78_000, config, orders)
        assert len(events) == 1
        e = events[0]
        assert e.event_type == RiskEventType.PRICE_DEPEG
        assert e.symbol == "BTC"
        assert e.drift_pct >= 0.05
        assert e.metadata["risk_classification"] == "gap_fill"
        assert e.metadata["drift_direction"] == "above"

    def test_sell_no_fill_risk(self, config):
        # BTC drops to $55k → projected $24.40, sell limit $31.00 → drift ≈ -21.3%
        orders = [_order("SELL", 31.00)]
        events = check_order_shadow_drift(55_000, config, orders)
        assert len(events) == 1
        e = events[0]
        assert e.metadata["risk_classification"] == "no_fill"
        assert e.metadata["drift_direction"] == "below"

    def test_generic_divergence(self, config):
        # BTC drops to $55k, buy limit $31.00 → drift below + buy side → diverged
        orders = [_order("BUY", 31.00)]
        events = check_order_shadow_drift(55_000, config, orders)
        assert len(events) == 1
        assert events[0].metadata["risk_classification"] == "diverged"

# ── RiskEvent integration ────────────────────────────────────────────────────

class TestRiskEventIntegration:
    def test_shadow_drift_produces_valid_risk_event(self, config):
        event = check_shadow_drift(78_000, config)
        assert isinstance(event, RiskEvent)
        assert event.event_type in RiskEventType
        assert event.timestamp is not None
        assert event.drift_pct > 0

    def test_order_drift_produces_valid_risk_events(self, config):
        orders = [_order("BUY", 31.00)]
        events = check_order_shadow_drift(78_000, config, orders)
        for event in events:
            assert isinstance(event, RiskEvent)
            assert event.event_type in RiskEventType
            assert event.timestamp is not None
