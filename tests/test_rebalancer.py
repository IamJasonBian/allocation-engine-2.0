"""Tests for the rebalancer observer — shadow depeg cancels and position flattening."""

import pytest

from app.enums import AssetType, OrderSide, RiskEventType
from app.risk.events import RiskEvent
from app.risk.rebalancer_observer import RebalancerObserver


@pytest.fixture
def rebalancer():
    return RebalancerObserver()


def _shadow_event(
    symbol: str = "BTC",
    drift_pct: float = 0.10,
    order_id: str = "ord-123",
    side: str = "SELL",
    limit_price: float = 33.00,
    projected_price: float = 30.01,
    quantity: float = 375,
    risk_classification: str = "no_fill",
) -> RiskEvent:
    return RiskEvent(
        event_type=RiskEventType.PRICE_DEPEG,
        symbol=symbol,
        drift_pct=drift_pct,
        message=f"test shadow depeg {symbol}",
        metadata={
            "order_id": order_id,
            "side": side,
            "quantity": quantity,
            "limit_price": limit_price,
            "projected_price": projected_price,
            "asset_type": AssetType.SHADOW_EQUITY,
            "risk_classification": risk_classification,
        },
    )


def _position_event(
    symbol: str = "AAPL",
    drift_pct: float = 0.12,
    position_qty: float = 100,
) -> RiskEvent:
    return RiskEvent(
        event_type=RiskEventType.PRICE_DEPEG,
        symbol=symbol,
        drift_pct=drift_pct,
        message=f"test position drift {symbol}",
        metadata={"position_qty": position_qty},
    )


class TestShadowDepeg:
    def test_queues_cancel_and_replace(self, rebalancer):
        rebalancer.on_risk_event(_shadow_event())
        orders, cancels = rebalancer.drain()
        assert len(cancels) == 1
        c = cancels[0]
        assert c["order_id"] == "ord-123"
        assert c["symbol"] == "BTC"
        assert c["side"] == "SELL"
        assert c["limit_price"] == 33.00
        assert "shadow_depeg" in c["reason"]
        # Replacement order at projected price
        assert len(orders) == 1
        o = orders[0]
        assert o["symbol"] == "BTC"
        assert o["side"] == "SELL"
        assert o["quantity"] == 375
        assert o["order_type"] == "limit"
        assert o["limit_price"] == 30.01
        assert "shadow_replace" in o["reason"]

    def test_no_order_id_skipped(self, rebalancer):
        event = RiskEvent(
            event_type=RiskEventType.PRICE_DEPEG,
            symbol="BTC",
            drift_pct=0.10,
            message="no order id",
            metadata={"asset_type": AssetType.SHADOW_EQUITY},
        )
        rebalancer.on_risk_event(event)
        orders, cancels = rebalancer.drain()
        assert cancels == []
        assert orders == []

    def test_no_projected_price_cancel_only(self, rebalancer):
        rebalancer.on_risk_event(_shadow_event(projected_price=0))
        orders, cancels = rebalancer.drain()
        assert len(cancels) == 1
        assert orders == []

    def test_multiple_cancel_and_replace(self, rebalancer):
        rebalancer.on_risk_event(_shadow_event(order_id="ord-1", limit_price=33.0, quantity=375))
        rebalancer.on_risk_event(_shadow_event(order_id="ord-2", limit_price=32.0, quantity=450))
        orders, cancels = rebalancer.drain()
        assert len(cancels) == 2
        assert {c["order_id"] for c in cancels} == {"ord-1", "ord-2"}
        assert len(orders) == 2
        assert {o["quantity"] for o in orders} == {375, 450}
        # All replacements at projected price
        assert all(o["limit_price"] == 30.01 for o in orders)


class TestPositionDrift:
    def test_queues_sell(self, rebalancer):
        rebalancer.on_risk_event(_position_event())
        orders, cancels = rebalancer.drain()
        assert cancels == []
        assert len(orders) == 1
        o = orders[0]
        assert o["symbol"] == "AAPL"
        assert o["side"] == OrderSide.SELL
        assert o["quantity"] == 100
        assert o["order_type"] == "market"

    def test_zero_qty_skipped(self, rebalancer):
        rebalancer.on_risk_event(_position_event(position_qty=0))
        orders, _ = rebalancer.drain()
        assert orders == []


class TestDrainClears:
    def test_drain_empties_both(self, rebalancer):
        rebalancer.on_risk_event(_shadow_event())
        rebalancer.on_risk_event(_position_event())
        orders, cancels = rebalancer.drain()
        assert len(orders) == 2  # shadow replace + position sell
        assert len(cancels) == 1
        # Second drain should be empty
        orders2, cancels2 = rebalancer.drain()
        assert orders2 == []
        assert cancels2 == []


class TestIgnoresOtherEvents:
    def test_ignores_non_depeg(self, rebalancer):
        event = RiskEvent(
            event_type=RiskEventType.ORDER_REJECTED,
            symbol="BTC",
            drift_pct=0.0,
            message="rejected",
        )
        rebalancer.on_risk_event(event)
        orders, cancels = rebalancer.drain()
        assert orders == []
        assert cancels == []
