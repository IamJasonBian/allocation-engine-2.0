"""Core allocation engine — reads desired state from the runtime service,
reconciles against Alpaca positions/orders, submits the delta via OTO pairing,
tracks fills in an order book, and uploads snapshots to the blob store."""

import logging

from runtime_client import RuntimeClient
from alpaca_client import AlpacaTrader
from order_book import OrderBook, audit_coverage
from blob_logger import build_snapshot, upload_snapshot

log = logging.getLogger(__name__)


class AllocationEngine:
    def __init__(self, dry_run: bool = False):
        self.runtime = RuntimeClient()
        self.trader = AlpacaTrader()
        self.dry_run = dry_run
        self.book = OrderBook()
        self._last_snapshot_key: str | None = None

    # ── public ───────────────────────────────────────────────────────

    def tick(self):
        """Single reconciliation cycle:
        1. Read desired state from runtime service
        2. Detect fills from previous tick
        3. Reconcile desired vs Alpaca state
        4. Execute orders (OTO pairing)
        5. Upload snapshot to blob store
        """
        state = self.runtime.state()
        snapshot_key = state.get("snapshot_key")

        # Always sync order book to detect fills, even if snapshot unchanged
        current_orders = self.trader.open_orders()
        current_positions = self.trader.positions()
        self.book.sync_with_alpaca(current_orders)

        book_summary = self.book.summary()
        if book_summary["fills_this_session"] > 0:
            log.info("Order book: %d fills this session", book_summary["fills_this_session"])

        if snapshot_key == self._last_snapshot_key:
            log.debug("No new snapshot (still %s), skipping order submission", snapshot_key)
            return

        log.info("Processing snapshot %s", snapshot_key)
        self._last_snapshot_key = snapshot_key

        desired_orders = self._desired_orders()

        new_orders, stale_order_ids = self._reconcile(
            desired_orders, current_orders, current_positions
        )

        execution_log = self._execute(new_orders, stale_order_ids)

        # Upload snapshot to blob store (gamma tag)
        self._upload_snapshot(
            desired_orders=desired_orders,
            execution_log=execution_log,
            drift_metrics=state.get("drift_metrics"),
        )

    def replace_all_orders(self):
        """Atomic order replacement: cancel everything, re-read desired, resubmit."""
        log.info("Atomic order replacement: cancelling all orders")
        self.trader.cancel_all()

        import time
        time.sleep(2)

        desired_orders = self._desired_orders()
        execution_log = self._execute(desired_orders, cancel_ids=[])

        self._upload_snapshot(
            desired_orders=desired_orders,
            execution_log=execution_log,
        )
        return execution_log

    def get_audit(self) -> dict:
        """Run coverage audit against live Alpaca state."""
        positions = self.trader.positions()
        orders = self.trader.open_orders()
        return audit_coverage(positions, orders)

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
        """
        def _order_key(o: dict) -> tuple:
            return (
                o.get("symbol"),
                o.get("side"),
                o.get("quantity") or o.get("qty"),
                o.get("limit_price"),
            )

        desired_keys = {_order_key(o) for o in desired}

        current_map: dict[tuple, dict] = {}
        for o in current_orders:
            key = (o["symbol"], o["side"], o["qty"], o["limit_price"])
            current_map[key] = o

        stale_ids = [
            o["id"] for key, o in current_map.items() if key not in desired_keys
        ]

        position_symbols = {p["symbol"] for p in current_positions}

        to_submit = []
        for o in desired:
            key = _order_key(o)
            if key in current_map:
                continue
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

    def _execute(self, orders: list[dict], cancel_ids: list[str]) -> list[dict]:
        """Cancel stale orders, then submit new ones using OTO pairing.

        Groups orders by symbol. For each symbol, SELL limits are paired
        with BUY limits as OTO orders (sell primary, buy triggers on fill).
        Unpaired sells go as simple limit orders. Unpaired buys are held
        back (Alpaca rejects them while sells are open on the same symbol).

        Returns execution log entries.
        """
        execution_log = []

        for oid in cancel_ids:
            if self.dry_run:
                log.info("[DRY RUN] Would cancel order %s", oid)
            else:
                self.trader.cancel_order(oid)
                execution_log.append({"action": "cancel", "order_id": oid})

        # Group by symbol
        by_symbol: dict[str, dict[str, list]] = {}
        for o in orders:
            sym = o["symbol"]
            if sym not in by_symbol:
                by_symbol[sym] = {"sells": [], "buys": []}
            if o["side"] == "SELL":
                by_symbol[sym]["sells"].append(o)
            else:
                by_symbol[sym]["buys"].append(o)

        for sym, sides in by_symbol.items():
            sells = sorted(sides["sells"], key=lambda o: o.get("limit_price") or 0)
            buys = sorted(sides["buys"], key=lambda o: -(o.get("limit_price") or 0))

            paired = min(len(sells), len(buys))

            # OTO pairs
            for i in range(paired):
                sell, buy = sells[i], buys[i]
                if self.dry_run:
                    log.info("[DRY RUN] OTO: SELL %s %s @ %s -> BUY @ %s",
                             sell["quantity"], sym, sell["limit_price"],
                             buy["limit_price"])
                else:
                    result = self.trader.submit_oto(sell, buy)
                    if result:
                        self.book.record_submit(
                            result["id"], sym, "SELL", float(sell["quantity"]),
                            float(sell["limit_price"]),
                            order_type="oto",
                            oto_buy_price=float(buy["limit_price"]),
                        )
                        execution_log.append({
                            "action": "submit_oto",
                            "symbol": sym,
                            "sell_price": sell["limit_price"],
                            "buy_price": buy["limit_price"],
                            "qty": sell["quantity"],
                            "alpaca_id": result["id"],
                        })

            # Unpaired sells
            for sell in sells[paired:]:
                if self.dry_run:
                    log.info("[DRY RUN] Simple SELL %s %s @ %s",
                             sell["quantity"], sym, sell["limit_price"])
                else:
                    result = self.trader.submit_order(sell)
                    if result:
                        self.book.record_submit(
                            result["id"], sym, "SELL", float(sell["quantity"]),
                            float(sell.get("limit_price", 0)),
                            order_type="simple",
                        )
                        execution_log.append({
                            "action": "submit_simple",
                            "symbol": sym,
                            "side": "SELL",
                            "price": sell.get("limit_price"),
                            "qty": sell["quantity"],
                            "alpaca_id": result["id"],
                        })

            # Unpaired buys: held back
            for buy in buys[paired:]:
                log.info("Held back BUY %s %s @ %s (no sell to pair as OTO)",
                         buy["quantity"], sym, buy.get("limit_price", "MKT"))
                execution_log.append({
                    "action": "held_back",
                    "symbol": sym,
                    "side": "BUY",
                    "price": buy.get("limit_price"),
                    "qty": buy["quantity"],
                    "reason": "no sell to pair as OTO",
                })

        return execution_log

    def _upload_snapshot(self, *, desired_orders: list[dict],
                         execution_log: list[dict] | None = None,
                         drift_metrics: dict | None = None):
        """Build and upload a gamma-tagged snapshot to the blob store."""
        if self.dry_run:
            log.info("[DRY RUN] Would upload snapshot")
            return

        positions = self.trader.positions()
        account = self.trader.account()
        alpaca_orders = self.trader.open_orders()

        oto_pairs = [
            e for e in (execution_log or []) if e.get("action") == "submit_oto"
        ]

        snapshot = build_snapshot(
            desired_orders=desired_orders,
            alpaca_orders=alpaca_orders,
            positions=positions,
            account=account,
            oto_pairs=oto_pairs,
            drift_metrics=drift_metrics,
            execution_log=execution_log,
        )

        upload_snapshot(snapshot)
