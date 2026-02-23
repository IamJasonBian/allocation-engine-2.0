"""Core allocation engine — reads desired state from the runtime service,
reconciles against Alpaca positions/orders, and submits the delta."""

import logging

from runtime_client import RuntimeClient
from alpaca_client import AlpacaTrader

log = logging.getLogger(__name__)


class AllocationEngine:
    def __init__(self, dry_run: bool = True):
        self.runtime = RuntimeClient()
        self.trader = AlpacaTrader()
        self.dry_run = dry_run
        self._last_snapshot_key: str | None = None

    # ── public ───────────────────────────────────────────────────────

    def tick(self):
        """Single reconciliation cycle: read desired state → diff → execute."""
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

    # ── internals ────────────────────────────────────────────────────

    def _desired_orders(self) -> list[dict]:
        """Fetch the target order set from the runtime service."""
        data = self.runtime.orders()
        return data.get("stock_orders", [])

    def _reconcile(
        self,
        desired: list[dict],
        current_orders: list[dict],
        current_positions: list[dict],
    ) -> tuple[list[dict], list[str]]:
        """Compare desired orders against Alpaca state.

        Returns (orders_to_submit, order_ids_to_cancel).

        Strategy:
        1. Build a set of desired (symbol, side, qty, limit_price) tuples.
        2. Match against open Alpaca orders — anything in Alpaca but not
           desired is stale and should be cancelled.
        3. Anything desired but not already open (and not already filled as
           a position) should be submitted.
        """
        def _order_key(o: dict) -> tuple:
            return (
                o.get("symbol"),
                o.get("side"),
                o.get("quantity") or o.get("qty"),
                o.get("limit_price"),
            )

        desired_keys = {_order_key(o) for o in desired}

        # Map current Alpaca orders by their key
        current_map: dict[tuple, dict] = {}
        for o in current_orders:
            key = (o["symbol"], o["side"], o["qty"], o["limit_price"])
            current_map[key] = o

        # Orders to cancel: in Alpaca but not in desired set
        stale_ids = [
            o["id"] for key, o in current_map.items() if key not in desired_keys
        ]

        # Position symbols for fill detection
        position_symbols = {p["symbol"] for p in current_positions}

        # Orders to submit: desired but not already open in Alpaca
        to_submit = []
        for o in desired:
            key = _order_key(o)
            if key in current_map:
                continue  # already open
            # Skip if we already hold a position on the same side
            if o["symbol"] in position_symbols and o["side"] == "BUY":
                log.info("Skipping %s BUY — already holding position", o["symbol"])
                continue
            to_submit.append(o)

        log.info(
            "Reconciliation: %d desired, %d already open, %d stale, %d to submit",
            len(desired), len(current_map) - len(stale_ids),
            len(stale_ids), len(to_submit),
        )
        return to_submit, stale_ids

    def _execute(self, orders: list[dict], cancel_ids: list[str]):
        """Cancel stale orders and submit new ones."""
        for oid in cancel_ids:
            if self.dry_run:
                log.info("[DRY RUN] Would cancel order %s", oid)
            else:
                self.trader.cancel_order(oid)

        results = []
        for order in orders:
            if self.dry_run:
                log.info("[DRY RUN] Would submit: %s %s %s @ %s",
                         order["side"], order["quantity"],
                         order["symbol"], order.get("limit_price", "MKT"))
            else:
                result = self.trader.submit_order(order)
                results.append(result)

        return results
