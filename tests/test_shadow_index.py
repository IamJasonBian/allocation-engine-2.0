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
    """Standard BTC Mini Trust config with a known Friday close."""
    return IndexConfig(
        shadow_symbol="BTC.shadow",
        crypto_symbol="BTC",
        btc_per_share=0.000367,
        last_close=31.05,
    )


@pytest.fixture
def config_no_close():
    return IndexConfig(
        shadow_symbol="BTC.shadow",
        crypto_symbol="BTC",
        btc_per_share=0.000367,
        last_close=None,
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
    def test_basic_conversion(self, config):
        # $84,500 BTC * 0.000367 ≈ $31.01
        price = btc_to_index_price(84_500, config)
        assert round(price, 2) == 31.01

    def test_zero_btc(self, config):
        assert btc_to_index_price(0, config) == 0.0


# ── build_shadow_position ───────────────────────────────────────────────────

class TestBuildShadowPosition:
    def test_position_shape(self, config):
        pos = build_shadow_position(84_500, config, qty=100)
        assert pos["symbol"] == "BTC.shadow"
        assert pos["asset_type"] == AssetType.SHADOW_EQUITY
        assert pos["qty"] == 100
        assert pos["current_price"] == pytest.approx(31.01, abs=0.02)
        assert pos["avg_entry"] == 31.05  # last_close used as entry

    def test_no_close_uses_projected_as_entry(self, config_no_close):
        pos = build_shadow_position(84_500, config_no_close, qty=10)
        assert pos["avg_entry"] == pos["current_price"]
        assert pos["unrealized_pl_pct"] == 0.0

    def test_zero_qty(self, config):
        pos = build_shadow_position(84_500, config, qty=0)
        assert pos["market_value"] == 0.0
        assert pos["unrealized_pl"] == 0.0

    def test_source_metadata(self, config):
        pos = build_shadow_position(90_000, config, qty=1)
        src = pos["_source"]
        assert src["crypto_symbol"] == "BTC"
        assert src["btc_price"] == 90_000
        assert src["btc_per_share"] == 0.000367
        assert src["last_close"] == 31.05


# ── check_shadow_drift ──────────────────────────────────────────────────────

class TestCheckShadowDrift:
    def test_no_close_returns_none(self, config_no_close):
        assert check_shadow_drift(84_500, config_no_close) is None

    def test_within_threshold_returns_none(self, config):
        assert check_shadow_drift(84_500, config) is None

    def test_above_threshold_emits_event(self, config):
        # BTC $95k → projected $34.87 vs close $31.05 → drift ≈ +12.3%
        event = check_shadow_drift(95_000, config)
        assert event is not None
        assert event.event_type == RiskEventType.PRICE_DEPEG
        assert event.symbol == "BTC.shadow"
        assert event.drift_pct >= 0.08
        assert event.metadata["direction"] == "above"

    def test_below_threshold_emits_event(self, config):
        # BTC $72k → projected $26.42 vs close $31.05 → drift ≈ -14.9%
        event = check_shadow_drift(72_000, config)
        assert event is not None
        assert event.drift_pct >= 0.08
        assert event.metadata["direction"] == "below"
        assert "below" in event.message

    def test_severity_levels(self, config):
        # Warning: 8-15%
        event = check_shadow_drift(95_000, config)
        assert event.severity == "warning"
        # Critical: ≥15%
        event = check_shadow_drift(105_000, config)
        assert event.severity == "critical"


# ── check_order_shadow_drift ────────────────────────────────────────────────

class TestCheckOrderShadowDrift:
    def test_no_btc_orders_returns_empty(self, config):
        orders = [_order("BUY", 150.0, symbol="AAPL")]
        events = check_order_shadow_drift(84_500, config, orders)
        assert events == []

    def test_within_threshold_returns_empty(self, config):
        orders = [_order("BUY", 31.00)]
        events = check_order_shadow_drift(84_500, config, orders)
        assert events == []

    def test_buy_gap_up_risk(self, config):
        # BTC $95k → projected $34.87, limit $31.00 → drift ≈ +12.5%
        orders = [_order("BUY", 31.00)]
        events = check_order_shadow_drift(95_000, config, orders)
        assert len(events) == 1
        e = events[0]
        assert e.event_type == RiskEventType.PRICE_DEPEG
        assert e.symbol == "BTC"
        assert e.drift_pct >= 0.05
        assert e.metadata["risk_classification"] == "gap_fill"
        assert e.metadata["drift_direction"] == "above"

    def test_sell_no_fill_risk(self, config):
        # BTC $72k → projected $26.42, sell limit $31.00 → drift ≈ -14.8%
        orders = [_order("SELL", 31.00)]
        events = check_order_shadow_drift(72_000, config, orders)
        assert len(events) == 1
        e = events[0]
        assert e.metadata["risk_classification"] == "no_fill"
        assert e.metadata["drift_direction"] == "below"

    def test_generic_divergence(self, config):
        # BTC $72k, buy limit $31.00 → drift below + buy side → diverged
        orders = [_order("BUY", 31.00)]
        events = check_order_shadow_drift(72_000, config, orders)
        assert len(events) == 1
        assert events[0].metadata["risk_classification"] == "diverged"

    def test_multiple_orders(self, config):
        orders = [
            _order("BUY", 31.00),
            _order("SELL", 30.50),
            _order("BUY", 150.00, symbol="AAPL"),
        ]
        events = check_order_shadow_drift(95_000, config, orders)
        assert len(events) == 2
        symbols = {e.symbol for e in events}
        assert symbols == {"BTC"}

    def test_no_limit_price_skipped(self, config):
        order = {"id": "x", "symbol": "BTC", "side": "BUY", "limit_price": None}
        events = check_order_shadow_drift(95_000, config, [order])
        assert events == []

    def test_zero_limit_price_skipped(self, config):
        orders = [_order("BUY", 0.0)]
        events = check_order_shadow_drift(95_000, config, orders)
        assert events == []

    def test_metadata_fields(self, config):
        orders = [_order("BUY", 31.00)]
        events = check_order_shadow_drift(95_000, config, orders)
        meta = events[0].metadata
        assert meta["order_id"] == "ord-BUY-31.0"
        assert meta["side"] == "BUY"
        assert meta["limit_price"] == 31.00
        assert meta["btc_price"] == 95_000
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
        event = check_shadow_drift(95_000, config)
        assert isinstance(event, RiskEvent)
        assert event.event_type in RiskEventType
        assert event.timestamp is not None
        assert event.drift_pct > 0

    def test_order_drift_produces_valid_risk_events(self, config):
        orders = [_order("BUY", 31.00)]
        events = check_order_shadow_drift(95_000, config, orders)
        for event in events:
            assert isinstance(event, RiskEvent)
            assert event.event_type in RiskEventType
            assert event.timestamp is not None
