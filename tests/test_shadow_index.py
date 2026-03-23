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
        crypto_symbol="BTC",
        last_close=31.05,
        btc_at_close=70_000,
    )


@pytest.fixture
def config_no_close():
    return IndexConfig(
        shadow_symbol="BTC.shadow",
        crypto_symbol="BTC",
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

    def test_zero_btc(self, config):
        assert btc_to_index_price(0, config) == 0.0


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

    def test_zero_qty(self, config):
        pos = build_shadow_position(70_000, config, qty=0)
        assert pos["market_value"] == 0.0
        assert pos["unrealized_pl"] == 0.0

    def test_source_metadata(self, config):
        pos = build_shadow_position(90_000, config, qty=1)
        src = pos["_source"]
        assert src["crypto_symbol"] == "BTC"
        assert src["btc_price"] == 90_000
        assert src["btc_at_close"] == 70_000
        assert src["last_close"] == 31.05


# ── check_shadow_drift ──────────────────────────────────────────────────────

class TestCheckShadowDrift:
    def test_no_close_returns_none(self, config_no_close):
        assert check_shadow_drift(70_000, config_no_close) is None

    def test_within_threshold_returns_none(self, config):
        # BTC unchanged → drift ≈ 0%
        assert check_shadow_drift(70_000, config) is None

    def test_above_threshold_emits_event(self, config):
        # BTC rallies to $78k → projected $34.59 vs close $31.05 → drift ≈ +11.4%
        event = check_shadow_drift(78_000, config)
        assert event is not None
        assert event.event_type == RiskEventType.PRICE_DEPEG
        assert event.symbol == "BTC.shadow"
        assert event.drift_pct >= 0.08
        assert event.metadata["direction"] == "above"

    def test_below_threshold_emits_event(self, config):
        # BTC drops to $60k → projected $26.61 vs close $31.05 → drift ≈ -14.3%
        event = check_shadow_drift(60_000, config)
        assert event is not None
        assert event.drift_pct >= 0.08
        assert event.metadata["direction"] == "below"
        assert "below" in event.message

    def test_severity_levels(self, config):
        # Warning: 8-15% drift
        event = check_shadow_drift(78_000, config)
        assert event.severity == "warning"
        # Critical: ≥15% drift
        event = check_shadow_drift(82_000, config)
        assert event.severity == "critical"


# ── check_order_shadow_drift ────────────────────────────────────────────────

class TestCheckOrderShadowDrift:
    def test_no_btc_orders_returns_empty(self, config):
        orders = [_order("BUY", 150.0, symbol="AAPL")]
        events = check_order_shadow_drift(70_000, config, orders)
        assert events == []

    def test_within_threshold_returns_empty(self, config):
        # projected ≈ $31.05, limit $31.00 → drift ≈ 0.16% (below 5%)
        orders = [_order("BUY", 31.00)]
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

    def test_multiple_orders(self, config):
        orders = [
            _order("BUY", 31.00),
            _order("SELL", 30.50),
            _order("BUY", 150.00, symbol="AAPL"),
        ]
        events = check_order_shadow_drift(78_000, config, orders)
        assert len(events) == 2
        symbols = {e.symbol for e in events}
        assert symbols == {"BTC"}

    def test_no_limit_price_skipped(self, config):
        order = {"id": "x", "symbol": "BTC", "side": "BUY", "limit_price": None}
        events = check_order_shadow_drift(78_000, config, [order])
        assert events == []

    def test_zero_limit_price_skipped(self, config):
        orders = [_order("BUY", 0.0)]
        events = check_order_shadow_drift(78_000, config, orders)
        assert events == []

    def test_metadata_fields(self, config):
        orders = [_order("BUY", 31.00)]
        events = check_order_shadow_drift(78_000, config, orders)
        meta = events[0].metadata
        assert meta["order_id"] == "ord-BUY-31.0"
        assert meta["side"] == "BUY"
        assert meta["limit_price"] == 31.00
        assert meta["btc_price"] == 78_000
        assert meta["asset_type"] == AssetType.SHADOW_EQUITY
        assert "projected_price" in meta


# ── RiskEvent integration ────────────────────────────────────────────────────

class TestRiskEventIntegration:
    def test_risk_event_type_values(self):
        assert RiskEventType.PRICE_DEPEG == "price_depeg"
        assert RiskEventType.POSITION_LIMIT == "position_limit"
        assert RiskEventType.ORDER_REJECTED == "order_rejected"

    def test_shadow_equity_asset_type(self):
        assert AssetType.SHADOW_EQUITY == "shadow_equity"

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
