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


def order_qty(order: dict) -> float | None:
    """Read an order's size from either key name, or None if it has neither.

    The runtime service emits `quantity`, the broker book emits `qty`, and both
    dict shapes reach the engine. Every size read goes through here so a
    key-name mismatch degrades to a skip rather than a KeyError mid-batch.

    Args:
        order: order dict from either source.

    Returns:
        The size as a float, or None when absent/unparseable.
    """
    raw = order.get("quantity")
    if raw is None:
        raw = order.get("qty")
    try:
        return float(raw)
    except (TypeError, ValueError):
        return None


def apply_order_limits(
    order: dict,
    max_qty: float | None = None,
    max_notional: float | None = None,
    price: float | None = None,
) -> tuple[dict | None, dict | None]:
    """Clamp one order to the share and dollar limits.

    Both limits are enforced against the *effective* size: the dollar cap uses
    the order's own `limit_price` when it has one, otherwise `price` (the last
    quote seen this tick). A market order with no resolvable price cannot be
    bounded in dollars, so it is skipped rather than submitted unbounded —
    fail closed, since an unpriced order is exactly the case the dollar cap
    exists to catch. With `max_notional` unset, that path never triggers and
    the share cap behaves as it always has.

    Args:
        order: desired order dict; not mutated.
        max_qty: maximum shares, or None for no share cap.
        max_notional: maximum dollar value, or None for no dollar cap.
        price: reference price used when the order carries no limit price.

    Returns:
        `(limited_order, adjustment)`. `limited_order` is None when the order
        must be dropped; `adjustment` is None when the order passed untouched,
        else a dict with `action` ("capped"/"skipped"), `reason`, and the
        requested/effective sizes for the reconciliation report.
    """
    qty = order_qty(order)
    symbol = order.get("symbol", "?")
    if qty is None or qty <= 0:
        return None, {
            "symbol": symbol, "side": order.get("side"), "action": "skipped",
            "reason": "no_quantity", "requested_qty": qty, "effective_qty": 0,
        }

    ref_price = order.get("limit_price") or price
    try:
        ref_price = float(ref_price) if ref_price else None
    except (TypeError, ValueError):
        ref_price = None

    effective, reason = qty, None

    if max_notional:
        if not ref_price or ref_price <= 0:
            return None, {
                "symbol": symbol, "side": order.get("side"), "action": "skipped",
                "reason": "unpriceable_under_notional_limit",
                "requested_qty": qty, "effective_qty": 0,
            }
        allowed = max_notional / ref_price
        if allowed < effective:
            effective, reason = allowed, "notional_limit"

    if max_qty is not None and max_qty < effective:
        effective, reason = float(max_qty), "qty_limit"

    # Round down — rounding up would breach the very limit being enforced.
    # Fractional sizes stay fractional; whole-share orders stay whole.
    if float(qty).is_integer():
        effective = float(int(effective))

    def _normalized(size: float) -> dict:
        # One size key survives, always `quantity`, so downstream reads and
        # the broker payload can't disagree about which key holds the size.
        out = dict(order)
        out["quantity"] = size
        out.pop("qty", None)
        return out

    if reason is None:
        return _normalized(qty), None

    if effective <= 0:
        return None, {
            "symbol": symbol, "side": order.get("side"), "action": "skipped",
            "reason": f"{reason}_below_one_unit", "requested_qty": qty,
            "effective_qty": 0, "limit_price": ref_price,
        }

    return _normalized(effective), {
        "symbol": symbol, "side": order.get("side"), "action": "capped",
        "reason": reason, "requested_qty": qty, "effective_qty": effective,
        "limit_price": ref_price,
    }


class AllocationEngine:
    def __init__(
        self,
        trader: BrokerClient,
        runtime: RuntimeClient,
        dry_run: bool = True,
        data_broker: BrokerClient | None = None,
        max_order_qty: int = 50,
        max_order_notional: float | None = None,
        risk_subject: RiskSubject | None = None,
    ):
        self.trader = trader          # execution broker (Robinhood)
        self.data_broker = data_broker  # market data broker (Alpaca), optional
        self.runtime = runtime
        self.dry_run = dry_run
        self.max_order_qty = max_order_qty
        # None/0 disables the dollar cap, so an unconfigured deploy behaves
        # exactly as it did before this limit existed.
        self.max_order_notional = float(max_order_notional or 0) or None
        self._last_snapshot_key: str | None = None
        self._prices: dict[str, float] = {}
        self._reported_adjustments: set[tuple] = set()
        # Last tick's limit/submission outcomes — read by the health endpoint
        # and the logs so a capped order is never a silent divergence.
        self.last_execution_report: dict = {}

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

        # Limits are applied to the *desired* set, before reconciliation, so a
        # capped order reconciles against its own open order. Capping inside
        # _execute() left the desired key (100 shares) permanently unmatched by
        # the open order it produced (50 shares), so every fresh snapshot
        # resubmitted another capped clone while cancellation stayed disabled.
        desired_orders, adjustments = self._limit_orders(desired_orders, snapshot_key)

        # --- drift check ---
        if market_data and current_positions:
            self._check_drift(current_positions, market_data, snapshot_key)

        new_orders, stale_order_ids = self._reconcile(
            desired_orders, current_orders, current_positions
        )

        # Append any rebalance orders / cancels queued by the rebalancer observer
        if self._rebalancer:
            rebalance_orders, rebalance_cancels = self._rebalancer.drain()
            if rebalance_orders:
                log.info("Adding %d rebalance order(s) from drift events", len(rebalance_orders))
                # Drift-triggered orders reach the broker through the same
                # submit path, so they take the same limits.
                rebalance_orders, rebalance_adj = self._limit_orders(
                    rebalance_orders, snapshot_key
                )
                adjustments.extend(rebalance_adj)
                new_orders.extend(rebalance_orders)
            if rebalance_cancels:
                log.info("Adding %d cancel(s) from shadow depeg events", len(rebalance_cancels))
                for cancel in rebalance_cancels:
                    stale_order_ids.append(cancel["order_id"])

        self._report_adjustments(adjustments, snapshot_key)
        self._execute(new_orders, stale_order_ids, adjustments)

        # --- Options order reconciliation (dry-run only) ---
        try:
            desired_opt_orders = self._desired_option_orders()
            if desired_opt_orders and hasattr(self.trader, "options_orders"):
                current_opt_orders = self.trader.options_orders(limit=200, open_only=True)
                opt_to_submit, opt_stale_ids = self._reconcile_option_orders(
                    desired_opt_orders, current_opt_orders
                )
                if opt_to_submit or opt_stale_ids:
                    self._execute_option_orders(opt_to_submit, opt_stale_ids)
        except Exception:
            log.exception("Options reconciliation failed — continuing")

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
                    # Cached so the dollar limit can price a market order,
                    # which carries no limit_price of its own.
                    self._prices[symbol.upper()] = float(price)
            return data
        except Exception:
            log.exception("Failed to fetch market data — skipping drift check")
            return {}

    def _limit_orders(
        self, orders: list[dict], snapshot_key: str | None,
    ) -> tuple[list[dict], list[dict]]:
        """Clamp every order to the share/dollar limits before reconciliation.

        Args:
            orders: desired orders as received from the runtime service.
            snapshot_key: snapshot the orders belong to, stamped onto events.

        Returns:
            `(orders_within_limits, adjustments)` — orders dropped entirely are
            absent from the first list and recorded in the second.
        """
        kept, adjustments = [], []
        for order in orders:
            limited, adjustment = apply_order_limits(
                order,
                max_qty=self.max_order_qty,
                max_notional=self.max_order_notional,
                price=self._prices.get(str(order.get("symbol", "")).upper()),
            )
            if adjustment:
                adjustments.append(adjustment)
            if limited is not None:
                kept.append(limited)
        return kept, adjustments

    def _report_adjustments(
        self, adjustments: list[dict], snapshot_key: str | None,
    ) -> None:
        """Log limit adjustments and publish the newly-appearing ones as events.

        The runtime service asked for a size the engine would not submit; that
        divergence rides the risk bus so operators (and the runtime service's
        own reconciliation) see it instead of inferring it from fill sizes.

        A standing over-limit request re-derives the same adjustment on every
        snapshot, so only adjustments that differ from the previous tick's set
        raise an event — the alert marks the divergence appearing or changing
        shape, not its continued existence. Every one is still logged.
        """
        current = set()
        for adjustment in adjustments:
            requested = adjustment.get("requested_qty") or 0
            effective = adjustment.get("effective_qty") or 0
            capped = adjustment["action"] == "capped"
            message = (
                f"{adjustment['symbol']} {adjustment.get('side') or '?'} "
                f"{'capped' if capped else 'skipped'}: requested {requested:g}"
                + (f" -> {effective:g}" if capped else "")
                + f" ({adjustment['reason']}; max_order_qty={self.max_order_qty}, "
                f"max_order_notional={self.max_order_notional})"
            )
            log.warning("ORDER LIMIT: %s", message)

            key = (adjustment["symbol"], adjustment.get("side"),
                   adjustment["action"], adjustment["reason"],
                   requested, effective)
            current.add(key)
            if key in self._reported_adjustments:
                continue

            # Shortfall as a fraction of the request, so RiskEvent.severity
            # reads a dropped order as critical and a trim as warning/info.
            shortfall = (requested - effective) / requested if requested else 1.0
            self.risk_subject.notify(RiskEvent(
                event_type=(RiskEventType.ORDER_CAPPED if capped
                            else RiskEventType.ORDER_SKIPPED),
                symbol=adjustment["symbol"],
                drift_pct=shortfall,
                message=message,
                snapshot_key=snapshot_key,
                metadata=dict(adjustment),
            ))
        self._reported_adjustments = current

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

    def _desired_option_orders(self) -> list[dict]:
        """Fetch desired option orders from the runtime service."""
        data = self.runtime.orders()
        return data.get("option_orders", [])

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
            # Both sides go through order_qty so the size is compared as a
            # float from whichever key the source used — "50" from the broker
            # book and 50 from the runtime service are the same order.
            return (
                o.get("symbol"),
                o.get("side"),
                order_qty(o),
                o.get("limit_price"),
            )

        desired_keys = {_order_key(o) for o in desired}

        current_map: dict[tuple, dict] = {}
        for o in current_orders:
            current_map[_order_key(o)] = o

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

    # -- options reconciliation ----------------------------------------------

    @staticmethod
    def _option_order_key(o: dict) -> tuple:
        """Canonical key for matching option orders.

        For desired orders: fields are top-level.
        For broker orders: fields are in legs[0].
        """
        leg = o.get("legs", [{}])[0] if o.get("legs") else {}
        return (
            (leg.get("chain_symbol") or o.get("chain_symbol", "")).upper(),
            (leg.get("option_type") or o.get("option_type", "")).lower(),
            float(leg.get("strike") or o.get("strike", 0)),
            leg.get("expiration") or o.get("expiration", ""),
            (leg.get("side") or o.get("side", "")).lower(),
            float(leg.get("quantity") or o.get("quantity", 0)),
        )

    def _reconcile_option_orders(
        self,
        desired: list[dict],
        current_orders: list[dict],
    ) -> tuple[list[dict], list[str]]:
        """Compare desired option orders against current broker option orders.

        Same pattern as equity _reconcile: returns (to_submit, stale_ids).
        Only considers open orders from the broker.
        """
        OPEN_STATES = {"queued", "confirmed", "partially_filled", "pending"}

        current_open = [o for o in current_orders if o.get("state") in OPEN_STATES]

        desired_keys = {self._option_order_key(o) for o in desired}

        current_map: dict[tuple, dict] = {}
        for o in current_open:
            current_map[self._option_order_key(o)] = o

        stale_ids = [
            o["order_id"] for key, o in current_map.items() if key not in desired_keys
        ]

        to_submit = []
        for o in desired:
            key = self._option_order_key(o)
            if key in current_map:
                continue
            to_submit.append(o)

        log.info(
            "Options reconciliation: %d desired, %d open, %d stale, %d to submit",
            len(desired), len(current_open) - len(stale_ids),
            len(stale_ids), len(to_submit),
        )
        return to_submit, stale_ids

    def _execute_option_orders(
        self, orders: list[dict], cancel_ids: list[str]
    ):
        """Log option order actions. Execution not yet implemented — always dry-run."""
        if cancel_ids:
            log.info("[OPTIONS] Found %d stale option order(s): %s", len(cancel_ids), cancel_ids)

        for order in orders:
            log.info(
                "[OPTIONS DRY RUN] Would submit: %s %s %s %.2f %s qty=%g @ %s",
                order.get("side", "?"),
                order.get("chain_symbol", "?"),
                order.get("option_type", "?"),
                float(order.get("strike", 0)),
                order.get("expiration", "?"),
                float(order.get("quantity", 0)),
                f"${order['limit_price']:,.2f}" if order.get("limit_price") else "MKT",
            )

    # -- equity execution ----------------------------------------------------

    def _execute(
        self,
        orders: list[dict],
        cancel_ids: list[str],
        adjustments: list[dict] | None = None,
    ):
        """Submit new orders and log stale ones. Cancellation is disabled.

        Orders arrive already clamped by `_limit_orders`. A broker failure on
        one order is logged and the batch continues — one rejected symbol must
        not strand every order behind it.

        Args:
            orders: orders to submit, already within the configured limits.
            cancel_ids: stale broker order ids (logged only — cancel disabled).
            adjustments: limit adjustments from this tick, for the report.

        Returns:
            The broker results for successfully submitted orders.
        """
        if cancel_ids:
            log.info("Found %d stale order(s) — cancellation disabled, skipping: %s",
                     len(cancel_ids), cancel_ids)

        results, failures = [], []
        for order in orders:
            if self.dry_run:
                otype = order.get("order_type", "market")
                lp = order.get("limit_price")
                price_str = f"${lp:,.2f}" if lp else "MKT"
                log.info("[DRY RUN] Would submit: %s %s %s %s @ %s",
                         order["side"], order["quantity"],
                         order["symbol"], otype, price_str)
                continue

            try:
                results.append(self.trader.submit_order(order))
            except Exception as e:  # noqa: BLE001 — one bad order, not the batch
                symbol = order.get("symbol", "?")
                log.exception("submit_order failed for %s — continuing batch", symbol)
                failures.append({"symbol": symbol, "side": order.get("side"),
                                 "quantity": order_qty(order), "error": str(e)})
                self.risk_subject.notify(RiskEvent(
                    event_type=RiskEventType.ORDER_REJECTED,
                    symbol=symbol,
                    drift_pct=1.0,
                    message=f"{symbol} submit_order failed: {e}",
                    snapshot_key=self._last_snapshot_key,
                    metadata={"order": {k: order.get(k) for k in
                                        ("symbol", "side", "quantity", "limit_price")}},
                ))

        adjustments = adjustments or []
        self.last_execution_report = {
            "snapshot_key": self._last_snapshot_key,
            "dry_run": self.dry_run,
            "submitted": len(results),
            "failed": failures,
            "capped": [a for a in adjustments if a["action"] == "capped"],
            "skipped": [a for a in adjustments if a["action"] == "skipped"],
        }
        if failures or adjustments:
            log.warning(
                "Execution report: %d submitted, %d failed, %d capped, %d skipped",
                self.last_execution_report["submitted"], len(failures),
                len(self.last_execution_report["capped"]),
                len(self.last_execution_report["skipped"]),
            )

        return results
