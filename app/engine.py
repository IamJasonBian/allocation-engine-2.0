"""Core allocation engine — reads desired state from the runtime service,
reconciles against broker positions/orders, and submits the delta."""

from __future__ import annotations

import logging

from app.brokers.base import BrokerClient
from app.enums import OrderSide, RiskEventType
from app.risk.events import RiskEvent
from app.risk.observer import RiskSubject
from app.risk.rebalancer_observer import RebalancerObserver
from app.runtime_client import RuntimeClient

log = logging.getLogger(__name__)

DRIFT_THRESHOLD = 0.08  # 8 %


class AllocationEngine:
    def __init__(
        self,
        trader: BrokerClient,
        runtime: RuntimeClient,
        dry_run: bool = True,
        data_broker: BrokerClient | None = None,
        max_order_qty: int = 50,
        risk_subject: RiskSubject | None = None,
    ):
        self.trader = trader          # execution broker (Robinhood)
        self.data_broker = data_broker  # market data broker (Alpaca), optional
        self.runtime = runtime
        self.dry_run = dry_run
        self.max_order_qty = max_order_qty
        self._last_snapshot_key: str | None = None

        # Risk event bus — observers attach here
        self.risk_subject = risk_subject or RiskSubject()
        self._rebalancer: RebalancerObserver | None = None

    def register_rebalancer(self, rebalancer: RebalancerObserver) -> None:
        """Attach a rebalancer so drift-triggered orders flow back into execution."""
        self._rebalancer = rebalancer
        self.risk_subject.attach(rebalancer)

    # -- public -------------------------------------------------------------

    def tick(self):
        """Single reconciliation cycle: read desired state -> drift check -> diff -> execute."""
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

        # --- fetch market data & log consolidated view ---
        market_data = self._fetch_market_data()
        self._log_tick_summary(current_positions, current_orders, market_data)

        # --- drift check ---
        if market_data and current_positions:
            self._check_drift(current_positions, market_data, snapshot_key)

        new_orders, stale_order_ids = self._reconcile(
            desired_orders, current_orders, current_positions
        )

        # Append any rebalance orders queued by the rebalancer observer
        if self._rebalancer:
            rebalance_orders = self._rebalancer.drain()
            if rebalance_orders:
                log.info("Adding %d rebalance order(s) from drift events", len(rebalance_orders))
                new_orders.extend(rebalance_orders)

        self._execute(new_orders, stale_order_ids)

    # -- internals ----------------------------------------------------------

    def _fetch_market_data(self) -> dict:
        """Pull market data from the runtime service. Returns ticker→metrics map."""
        try:
            data = self.runtime.market_data()
            # Broadcast price updates to observers
            for symbol, metrics in data.get("tickers", {}).items():
                price = metrics.get("price")
                if price is not None:
                    self.risk_subject.set_price(symbol, float(price))
            return data
        except Exception:
            log.exception("Failed to fetch market data — skipping drift check")
            return {}

    def _log_tick_summary(
        self,
        positions: list[dict],
        open_orders: list[dict],
        market_data: dict,
    ) -> None:
        """Log a consolidated view of market prices, positions, and open orders each tick.

        Produces one table per tick so operators can visually cross-reference
        market price vs entry price vs order limit price and spot drift.
        """
        tickers = market_data.get("tickers", {})

        # Collect every symbol we know about
        all_symbols: set[str] = set()
        pos_map: dict[str, dict] = {}
        for p in positions:
            sym = p["symbol"]
            all_symbols.add(sym)
            pos_map[sym] = p

        order_map: dict[str, list[dict]] = {}
        for o in open_orders:
            sym = o["symbol"]
            all_symbols.add(sym)
            order_map.setdefault(sym, []).append(o)

        all_symbols.update(tickers.keys())

        if not all_symbols:
            log.info("No positions, orders, or market data to display")
            return

        # Header
        log.info(
            "%-6s  %10s  %10s  %6s  %8s  %10s  %6s  %s",
            "SYM", "MKT PRICE", "ENTRY", "QTY", "MV", "ORD PRICE", "DRIFT", "FLAG",
        )
        log.info("-" * 80)

        for sym in sorted(all_symbols):
            mkt = tickers.get(sym, {})
            pos = pos_map.get(sym)
            orders = order_map.get(sym, [])

            mkt_price = mkt.get("price")
            drift = mkt.get("drift_pct", 0)
            flag = "DRIFT" if abs(drift) >= DRIFT_THRESHOLD else ""

            mkt_str = f"${mkt_price:,.2f}" if mkt_price is not None else "-"
            entry_str = f"${pos['avg_entry']:,.2f}" if pos and pos.get("avg_entry") else "-"
            qty_str = str(pos["qty"]) if pos else "-"
            mv_str = f"${pos['market_value']:,.2f}" if pos and pos.get("market_value") is not None else "-"
            drift_str = f"{drift:+.2%}" if mkt else "-"

            if orders:
                for o in orders:
                    lp = o.get("limit_price")
                    ord_str = f"${lp:,.2f}" if lp else "MKT"
                    ord_label = f"{o['side']} {o['qty']}@{ord_str}"
                    log.info(
                        "%-6s  %10s  %10s  %6s  %8s  %10s  %6s  %s",
                        sym, mkt_str, entry_str, qty_str, mv_str, ord_label, drift_str, flag,
                    )
                    # Only show position columns on the first row for this symbol
                    entry_str = ""
                    qty_str = ""
                    mv_str = ""
            else:
                log.info(
                    "%-6s  %10s  %10s  %6s  %8s  %10s  %6s  %s",
                    sym, mkt_str, entry_str, qty_str, mv_str, "-", drift_str, flag,
                )

        log.info("-" * 80)

    def _check_drift(
        self,
        positions: list[dict],
        market_data: dict,
        snapshot_key: str | None,
    ) -> None:
        """Compare position drift against threshold; emit risk events if breached."""
        tickers = market_data.get("tickers", {})
        for pos in positions:
            symbol = pos["symbol"]
            metrics = tickers.get(symbol)
            if not metrics:
                continue
            drift = abs(metrics.get("drift_pct", 0))
            if drift >= DRIFT_THRESHOLD:
                event = RiskEvent(
                    event_type=RiskEventType.PRICE_DEPEG,
                    symbol=symbol,
                    drift_pct=drift,
                    message=(
                        f"{symbol} drift {drift:.2%} exceeds {DRIFT_THRESHOLD:.0%} threshold "
                        f"— possible structural change"
                    ),
                    snapshot_key=snapshot_key,
                    metadata={"position_qty": pos.get("qty", 0)},
                )
                log.warning("RISK EVENT: %s", event.message)
                self.risk_subject.notify(event)

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
        """Compare desired orders against broker state.

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

        to_submit = []
        for o in desired:
            key = _order_key(o)
            if key in current_map:
                continue
            to_submit.append(o)

        log.info(
            "Reconciliation: %d desired, %d already open, %d stale, %d to submit",
            len(desired), len(current_map) - len(stale_ids),
            len(stale_ids), len(to_submit),
        )
        return to_submit, stale_ids

    def _execute(self, orders: list[dict], cancel_ids: list[str]):
        """Log stale orders and submit new ones. Cancellation is disabled."""
        if cancel_ids:
            log.info("Found %d stale order(s) — cancellation disabled, skipping: %s",
                     len(cancel_ids), cancel_ids)

        results = []
        for order in orders:
            # Enforce max order quantity
            qty = order["quantity"]
            if qty > self.max_order_qty:
                log.warning(
                    "Order capped: %s %s qty %g -> %d (max_order_qty=%d)",
                    order["side"], order["symbol"], qty,
                    self.max_order_qty, self.max_order_qty,
                )
                order["quantity"] = self.max_order_qty

            if self.dry_run:
                otype = order.get("order_type", "market")
                lp = order.get("limit_price")
                price_str = f"${lp:,.2f}" if lp else "MKT"
                log.info("[DRY RUN] Would submit: %s %s %s %s @ %s",
                         order["side"], order["quantity"],
                         order["symbol"], otype, price_str)
            else:
                result = self.trader.submit_order(order)
                results.append(result)

        return results
