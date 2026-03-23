"""Rebalancer observer — reacts to PRICE_DEPEG by queuing cancellations or rebalance orders."""

from __future__ import annotations

import logging

from app.enums import AssetType, OrderSide, RiskEventType
from app.risk.events import RiskEvent
from app.risk.observer import RiskObserver

log = logging.getLogger(__name__)


class RebalancerObserver(RiskObserver):
    """On PRICE_DEPEG, queues corrective actions for the engine to execute.

    Two modes based on the event's asset_type metadata:

    1. **Shadow equity** (weekend order depeg) — queues a cancel for the
       stale limit order so it doesn't fill at a bad price on Monday open.
    2. **Regular equity** — queues a market sell to flatten the drifted position.

    Actions are collected in ``pending_orders`` and ``pending_cancels`` so the
    engine can pick them up on the next execution pass.
    """

    def __init__(self) -> None:
        self.pending_orders: list[dict] = []
        self.pending_cancels: list[dict] = []

    def update(self, symbol: str, price: float) -> None:
        """Price update — rebalancer only acts on risk events, not raw prices."""
        pass

    def on_risk_event(self, event: RiskEvent) -> None:
        if event.event_type != RiskEventType.PRICE_DEPEG:
            return

        asset_type = event.metadata.get("asset_type")

        if asset_type == AssetType.SHADOW_EQUITY:
            self._handle_shadow_depeg(event)
        else:
            self._handle_position_drift(event)

    def _handle_shadow_depeg(self, event: RiskEvent) -> None:
        """Cancel a stale limit order and replace it at the projected price."""
        order_id = event.metadata.get("order_id")
        if not order_id:
            log.info("Shadow depeg for %s but no order_id in metadata, skipping", event.symbol)
            return

        side = event.metadata.get("side", "?")
        old_limit = event.metadata.get("limit_price")
        projected = event.metadata.get("projected_price")

        cancel = {
            "order_id": order_id,
            "symbol": event.symbol,
            "side": side,
            "limit_price": old_limit,
            "reason": f"shadow_depeg:{event.drift_pct:.2%}",
            "risk_classification": event.metadata.get("risk_classification", ""),
        }
        self.pending_cancels.append(cancel)

        # Replace with a new limit order at the projected shadow price
        if projected and projected > 0:
            qty = event.metadata.get("quantity", 0)
            replacement = {
                "symbol": event.symbol,
                "side": side,
                "quantity": qty,
                "order_type": "limit",
                "limit_price": round(projected, 2),
                "reason": f"shadow_replace:{old_limit}->{round(projected, 2)}",
            }
            self.pending_orders.append(replacement)
            log.warning(
                "Replace queued: %s %s limit $%s → $%s (drift=%s)",
                side, event.symbol, old_limit, round(projected, 2),
                f"{event.drift_pct:.2%}",
            )
        else:
            log.warning(
                "Cancel queued (no replace — missing projected price): %s %s limit $%s",
                side, event.symbol, old_limit,
            )

    def _handle_position_drift(self, event: RiskEvent) -> None:
        """Flatten a drifted position with a market sell."""
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
            "reason": f"price_depeg:{event.drift_pct:.2%}",
        }
        self.pending_orders.append(order)
        log.warning(
            "Rebalance queued: SELL %s %s (drift=%s)",
            position_qty, event.symbol, f"{event.drift_pct:.2%}",
        )

    def drain(self) -> tuple[list[dict], list[dict]]:
        """Return and clear all pending rebalance orders and cancels."""
        orders = self.pending_orders
        cancels = self.pending_cancels
        self.pending_orders = []
        self.pending_cancels = []
        return orders, cancels
