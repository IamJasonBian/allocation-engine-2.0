"""Core allocation engine — reads desired state from the runtime service,
reconciles against broker positions/orders, and submits the delta."""

import logging

from app.brokers.base import BrokerClient
from app.models import OpenOrder, Order, Position
from app.runtime_client import RuntimeClient

log = logging.getLogger(__name__)


class AllocationEngine:
    def __init__(self, trader: BrokerClient, runtime: RuntimeClient, dry_run: bool = True):
        self.trader = trader
        self.runtime = runtime
        self.dry_run = dry_run
        self._last_snapshot_key: str | None = None

    # -- public -------------------------------------------------------------

    def tick(self):
        """Single reconciliation cycle: read desired state -> diff -> execute."""
        state = self.runtime.state()
        snapshot_key = state.get("snapshot_key")

        if snapshot_key == self._last_snapshot_key:
            log.debug("No new snapshot (still %s), skipping", snapshot_key)
            return

        log.info("Processing snapshot %s", snapshot_key)
        self._last_snapshot_key = snapshot_key

        desired_orders = self._desired_orders()
        current_orders = self.trader.open_orders()
        current_positions = self.trader.positions()

        new_orders, stale_order_ids = self._reconcile(
            desired_orders, current_orders, current_positions
        )

        self._execute(new_orders, stale_order_ids)

    # -- internals ----------------------------------------------------------

    def _desired_orders(self) -> list[Order]:
        """Fetch the target order set from the runtime service."""
        data = self.runtime.orders()
        return [
            Order(
                symbol=o["symbol"],
                side=o["side"],
                qty=float(o.get("quantity") or o.get("qty", 0)),
                order_type=o.get("order_type", "market"),
                limit_price=float(o["limit_price"]) if o.get("limit_price") else None,
                stop_price=float(o["stop_price"]) if o.get("stop_price") else None,
            )
            for o in data.get("stock_orders", [])
        ]

    @staticmethod
    def _order_key(symbol: str, side: str, qty: float, limit_price: float | None) -> tuple:
        return (symbol, side, qty, limit_price)

    def _reconcile(
        self,
        desired: list[Order],
        current_orders: list[OpenOrder],
        current_positions: list[Position],
    ) -> tuple[list[Order], list[str]]:
        """Compare desired orders against broker state.

        Returns (orders_to_submit, order_ids_to_cancel).
        """
        desired_keys = {
            self._order_key(o.symbol, o.side, o.qty, o.limit_price)
            for o in desired
        }

        current_map: dict[tuple, OpenOrder] = {}
        for o in current_orders:
            key = self._order_key(o.symbol, o.side, o.qty, o.limit_price)
            current_map[key] = o

        stale_ids = [
            o.id for key, o in current_map.items() if key not in desired_keys
        ]

        position_symbols = {p.symbol for p in current_positions}

        to_submit: list[Order] = []
        for o in desired:
            key = self._order_key(o.symbol, o.side, o.qty, o.limit_price)
            if key in current_map:
                continue
            if o.symbol in position_symbols and o.side == "BUY":
                log.info("Skipping %s BUY — already holding position", o.symbol)
                continue
            to_submit.append(o)

        log.info(
            "Reconciliation: %d desired, %d already open, %d stale, %d to submit",
            len(desired), len(current_map) - len(stale_ids),
            len(stale_ids), len(to_submit),
        )
        return to_submit, stale_ids

    def _execute(self, orders: list[Order], cancel_ids: list[str]):
        """Log stale orders and submit new ones. Cancellation is disabled."""
        if cancel_ids:
            log.info("Found %d stale order(s) — cancellation disabled, skipping: %s",
                     len(cancel_ids), cancel_ids)

        results = []
        for order in orders:
            if self.dry_run:
                log.info("[DRY RUN] Would submit: %s %s %s @ %s",
                         order.side, order.qty,
                         order.symbol, order.limit_price or "MKT")
            else:
                result = self.trader.submit_order(order)
                results.append(result)

        return results
