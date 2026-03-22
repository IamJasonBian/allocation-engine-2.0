"""Rebalancer observer — reacts to structural drift by queuing rebalance orders."""

from __future__ import annotations

import logging

from app.enums import OrderSide, RiskEventType
from app.risk.events import RiskEvent
from app.risk.observer import RiskObserver

log = logging.getLogger(__name__)


class RebalancerObserver(RiskObserver):
    """On STRUCTURAL_DRIFT, generates a market sell to flatten the drifted position.

    Rebalance orders are collected in ``pending_orders`` so the engine can
    pick them up on the next execution pass.
    """

    def __init__(self) -> None:
        self.pending_orders: list[dict] = []

    def update(self, symbol: str, price: float) -> None:
        """Price update — rebalancer only acts on risk events, not raw prices."""
        pass

    def on_risk_event(self, event: RiskEvent) -> None:
        if event.event_type != RiskEventType.STRUCTURAL_DRIFT:
            return

        position_qty = event.metadata.get("position_qty", 0)
        if position_qty <= 0:
            log.info("No position to rebalance for %s, skipping", event.symbol)
            return

        order = {
            "symbol": event.symbol,
            "side": OrderSide.SELL,
            "quantity": position_qty,
            "order_type": "market",
            "limit_price": None,
            "reason": f"structural_drift:{event.drift_pct:.2%}",
        }
        self.pending_orders.append(order)
        log.warning(
            "Rebalance queued: SELL %s %s (drift=%s)",
            position_qty, event.symbol, f"{event.drift_pct:.2%}",
        )

    def drain(self) -> list[dict]:
        """Return and clear all pending rebalance orders."""
        orders = self.pending_orders
        self.pending_orders = []
        return orders
