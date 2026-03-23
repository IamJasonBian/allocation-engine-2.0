"""Background engine thread — runs reconciliation loop alongside Flask."""

import os
import threading
import time
import logging
from datetime import datetime, timezone
from typing import TypedDict

from app.enums import (
    OrderSide, OrderType, AssetType, OrderState, OrderTrigger, OPEN_STATES,
)

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Unified OrderEvent — a single shape for both equity and option orders
# ---------------------------------------------------------------------------

class OrderEvent(TypedDict, total=False):
    """Normalised order record used by Redis/Blob sync and the API layer."""
    id: str
    symbol: str
    side: OrderSide
    order_type: OrderType
    asset_type: AssetType
    trigger: OrderTrigger
    state: OrderState | str
    quantity: float
    filled_quantity: float
    limit_price: float | None
    stop_price: float | None
    price: float | None                   # average fill price
    created_at: str
    updated_at: str
    # option-specific fields (absent for equities)
    legs: list[dict] | None
    direction: str | None                 # debit / credit
    opening_strategy: str | None
    premium: float | None
    processed_premium: float | None

def _equity_order_to_event(o: dict, *, is_open: bool = False) -> OrderEvent:
    """Convert a stock order dict (from broker) into an OrderEvent."""
    raw_type = o.get("type", "market")
    return OrderEvent(
        id=o.get("id", ""),
        symbol=o.get("symbol", ""),
        side=o.get("side", "").upper(),
        order_type=raw_type,
        asset_type=AssetType.EQUITY,
        trigger=OrderTrigger.STOP if raw_type in (OrderType.STOP, OrderType.STOP_LIMIT) else OrderTrigger.IMMEDIATE,
        state=o.get("status") or o.get("state", OrderState.UNKNOWN),
        quantity=float(o.get("qty", 0) or o.get("quantity", 0)),
        filled_quantity=float(o.get("filled_quantity", 0)),
        limit_price=o.get("limit_price"),
        stop_price=o.get("stop_price"),
        price=o.get("price"),
        created_at=o.get("created_at", ""),
        updated_at=o.get("updated_at", ""),
        legs=None,
        direction=None,
        opening_strategy=None,
        premium=None,
        processed_premium=None,
    )


def _option_order_to_event(o: dict) -> OrderEvent:
    """Convert an options order dict (from broker) into an OrderEvent."""
    legs = o.get("legs", [])
    symbol = o.get("chain_symbol", "")
    if not symbol and legs:
        symbol = legs[0].get("chain_symbol", "")

    return OrderEvent(
        id=o.get("order_id", o.get("id", "")),
        symbol=symbol,
        side=o.get("direction", "").upper(),
        order_type=o.get("order_type", o.get("type", OrderType.LIMIT)),
        asset_type=AssetType.OPTION,
        trigger=o.get("trigger", OrderTrigger.IMMEDIATE),
        state=o.get("state", OrderState.UNKNOWN),
        quantity=float(o.get("quantity", 0)),
        filled_quantity=float(o.get("processed_quantity", 0)),
        limit_price=float(o["price"]) if o.get("price") else None,
        stop_price=None,
        price=float(o["premium"]) if o.get("premium") else None,
        created_at=o.get("created_at", ""),
        updated_at=o.get("updated_at", ""),
        legs=legs if legs else None,
        direction=o.get("direction", ""),
        opening_strategy=o.get("opening_strategy") or o.get("closing_strategy") or None,
        premium=float(o["premium"]) if o.get("premium") else None,
        processed_premium=float(o["processed_premium"]) if o.get("processed_premium") else None,
    )


_engine_thread = None
_engine_status = {
    "running": False,
    "last_tick": None,
    "tick_count": 0,
    "last_error": None,
    "dry_run": True,
}
_tick_event = threading.Event()


def start_engine_thread(app):
    """Start the background reconciliation loop in a daemon thread."""
    global _engine_thread

    if _engine_thread and _engine_thread.is_alive():
        return

    def _loop():
        with app.app_context():
            try:
                from app.brokers import get_broker, clear_broker
                from app.brokers.robinhood_client import RobinhoodTrader, seconds_until_hour_et
                from app.engine import AllocationEngine
                from app.runtime_client import RuntimeClient
                from app.redis_store import sync_to_redis
                from app.blob_store import sync_to_blob
                from app.s3_store import sync_order_events
                from app.slack import notify as slack_notify
                from app.risk.observer import RiskSubject
                from app.risk.slack_observer import SlackAlertObserver
                from app.shadow_index import (
                    BTC_MINI, build_shadow_position, check_shadow_drift,
                    check_order_shadow_drift,
                )
            except Exception as e:
                log.exception("Engine thread failed during imports")
                _engine_status["last_error"] = f"import error: {e}"
                return

            config = app.config
            broker = None
            data_broker = None
            engine = None
            runtime = RuntimeClient(config["RUNTIME_SERVICE_URL"])

            # Risk infrastructure
            risk_subject = RiskSubject()
            webhook_url = config.get("SLACK_WEBHOOK_URL") or os.environ.get("SLACK_WEBHOOK_URL")
            if webhook_url:
                risk_subject.attach(SlackAlertObserver(webhook_url))
                log.info("SlackAlertObserver attached to background engine")

            # Shadow index config — project BTC/USD → Grayscale Bitcoin Mini Trust ETF
            shadow_index = BTC_MINI
            etf_close = os.environ.get("BTC_ETF_LAST_CLOSE")
            btc_at_close = os.environ.get("BTC_AT_CLOSE")
            if etf_close and btc_at_close:
                shadow_index.last_close = float(etf_close)
                shadow_index.btc_at_close = float(btc_at_close)
                log.info("Shadow index %s configured (last_close=$%.2f, btc_at_close=$%,.2f)",
                         shadow_index.shadow_symbol, shadow_index.last_close,
                         shadow_index.btc_at_close)

            _engine_status["running"] = True
            _engine_status["dry_run"] = config["DRY_RUN"]
            interval = config["POLL_INTERVAL_SECONDS"]
            is_live = not config["DRY_RUN"]
            blob_interval = 15 * 60  # 15 minutes
            last_blob_sync = 0.0
            retry_hour = config.get("RH_RETRY_HOUR_ET", 11)

            data_broker_name = config.get("DATA_BROKER", "")
            log.info("Background engine started (interval=%ds, dry_run=%s, broker=%s, data_broker=%s)",
                     interval, config["DRY_RUN"], config["ENGINE_BROKER"],
                     data_broker_name or "none")

            positions = []
            open_orders = []
            account = {}

            while True:
                # --- Broker initialization ---
                if broker is None:
                    try:
                        broker = get_broker(config["ENGINE_BROKER"])
                        if data_broker_name and data_broker_name != config["ENGINE_BROKER"]:
                            try:
                                data_broker = get_broker(data_broker_name)
                                log.info("Data broker (%s) initialized", data_broker_name)
                            except Exception:
                                log.exception("Failed to init data broker (%s) — continuing without",
                                              data_broker_name)
                                data_broker = None
                        engine = AllocationEngine(
                            trader=broker,
                            runtime=runtime,
                            dry_run=config["DRY_RUN"],
                            data_broker=data_broker,
                            max_order_qty=config["MAX_ORDER_QTY"],
                            risk_subject=risk_subject,
                        )
                        log.info("Broker initialized successfully")
                    except Exception as e:
                        log.exception("Failed to initialize broker")
                        _engine_status["last_error"] = str(e)
                        _tick_event.wait(timeout=interval)
                        _tick_event.clear()
                        continue

                # --- Normal tick ---
                try:
                    if is_live:
                        log.info("Live mode: refreshing broker state")
                    else:
                        engine.tick()

                    _engine_status["last_tick"] = datetime.now(timezone.utc).isoformat()
                    _engine_status["tick_count"] += 1
                    _engine_status["last_error"] = None

                    positions = broker.positions()
                    open_orders = broker.open_orders()
                    account = broker.account()

                    # --- Fetch options positions & orders ---
                    options_positions = []
                    options_open_orders: list[OrderEvent] = []
                    if hasattr(broker, "options_positions"):
                        try:
                            options_positions = broker.options_positions()
                        except Exception:
                            log.exception("Failed to fetch options positions")
                    if hasattr(broker, "options_orders"):
                        try:
                            raw_opt_orders = broker.options_orders(limit=200)
                            for oo in raw_opt_orders:
                                options_open_orders.append(_option_order_to_event(oo))
                        except Exception:
                            log.exception("Failed to fetch options orders")

                    # --- Build unified OrderEvent lists ---
                    equity_events: list[OrderEvent] = [
                        _equity_order_to_event(o, is_open=True) for o in open_orders
                    ]
                    all_order_events = equity_events + options_open_orders

                    # Enrich positions with Alpaca market data prices
                    if data_broker and hasattr(data_broker, "get_latest_prices") and positions:
                        try:
                            syms = [p["symbol"] for p in positions]
                            prices = data_broker.get_latest_prices(syms)
                            for p in positions:
                                sym = p["symbol"]
                                if sym in prices:
                                    price = prices[sym]
                                    qty = p["qty"]
                                    p["current_price"] = price
                                    p["market_value"] = round(qty * price, 2)
                                    cost_basis = qty * p["avg_entry"]
                                    p["unrealized_pl"] = round(qty * price - cost_basis, 2)
                                    p["unrealized_pl_pct"] = round(
                                        (qty * price - cost_basis) / cost_basis, 4
                                    ) if cost_basis > 0 else 0.0
                            log.info("[data] Enriched %d/%d positions with Alpaca prices",
                                     len(prices), len(positions))
                        except Exception:
                            log.exception("Failed to enrich positions with Alpaca prices")

                    # --- Shadow equity: project BTC → GBTC index drift ---
                    if shadow_index and shadow_index.last_close and shadow_index.btc_at_close and data_broker:
                        btc_pos = next(
                            (p for p in positions if p["symbol"] == shadow_index.crypto_symbol),
                            None,
                        )
                        if btc_pos:
                            try:
                                btc_prices = data_broker.get_latest_prices(
                                    [shadow_index.crypto_symbol]
                                )
                                btc_px = btc_prices.get(shadow_index.crypto_symbol)
                                if btc_px:
                                    shadow_pos = build_shadow_position(
                                        btc_px, shadow_index, qty=btc_pos["qty"],
                                    )
                                    log.info(
                                        "[shadow] %s projected $%.2f (BTC $%,.2f) "
                                        "vs close $%.2f → drift %+.2f%%",
                                        shadow_index.shadow_symbol,
                                        shadow_pos["current_price"], btc_px,
                                        shadow_index.last_close,
                                        shadow_pos["unrealized_pl_pct"] * 100,
                                    )
                                    event = check_shadow_drift(btc_px, shadow_index)
                                    if event:
                                        risk_subject.notify(event)
                                    # Check open limit orders against projected price
                                    order_events = check_order_shadow_drift(
                                        btc_px, shadow_index, open_orders,
                                    )
                                    for oe in order_events:
                                        risk_subject.notify(oe)
                            except Exception:
                                log.exception("Shadow index check failed")

                    log.info(
                        f"[portfolio] Equity: ${account.get('equity', 0):,.2f} | "
                        f"Cash: ${account.get('cash', 0):,.2f} | "
                        f"Buying Power: ${account.get('buying_power', 0):,.2f} | "
                        f"Market Value: ${account.get('portfolio_value', 0):,.2f}"
                    )

                    if open_orders:
                        for o in open_orders:
                            log.info("[order] %s %s — %s qty=%g limit=$%s status=%s",
                                     o.get("side", "?"), o.get("symbol", "?"),
                                     o.get("type", "market"),
                                     o.get("qty", 0),
                                     o.get("limit_price") or "MKT",
                                     o.get("status", "?"))
                    else:
                        log.info("[order] No open orders")

                    if options_positions:
                        log.info("[options] %d option positions, %d option orders",
                                 len(options_positions), len(options_open_orders))

                    # Sync to Redis (now with options)
                    try:
                        sync_to_redis(
                            positions, open_orders, account,
                            live=is_live,
                            options_positions=options_positions,
                            order_events=all_order_events,
                        )
                    except Exception:
                        log.exception("Redis sync error")

                    now_mono = time.monotonic()
                    if is_live and (now_mono - last_blob_sync) >= blob_interval:
                        try:
                            sync_to_blob(
                                positions, open_orders, account,
                                options_positions=options_positions,
                                option_orders=options_open_orders,
                            )
                            last_blob_sync = now_mono
                        except Exception:
                            log.exception("Blob sync error")

                        # Sync order events to S3
                        try:
                            sync_order_events(
                                all_order_events,
                                positions=positions,
                                options_positions=options_positions,
                                account=account,
                            )
                        except Exception:
                            log.exception("S3 sync error")

                except Exception as e:
                    log.exception("Engine tick error")
                    _engine_status["last_error"] = str(e)

                    # If Robinhood is stuck in a device challenge, sleep until
                    # the configured retry hour instead of retrying every tick.
                    if (config["ENGINE_BROKER"] == "robinhood"
                            and isinstance(broker, RobinhoodTrader)
                            and broker.in_device_challenge_mode):
                        wait_secs = seconds_until_hour_et(retry_hour)
                        log.info("[scheduler] Device challenge mode — "
                                 "sleeping %.0f seconds until %d:00 AM ET",
                                 wait_secs, retry_hour)
                        slack_notify(
                            f":clock11: FlipActivate: allocation-engine-2.0 — "
                            f"Device challenge pending. Will retry at "
                            f"{retry_hour}:00 AM ET "
                            f"(in {wait_secs / 3600:.1f} hours). "
                            "Approve the device in the Robinhood app before then."
                        )
                        _tick_event.wait(timeout=wait_secs)
                        _tick_event.clear()
                        # Force fresh broker on next iteration
                        clear_broker(config["ENGINE_BROKER"])
                        broker = None
                        engine = None
                        continue

                _tick_event.wait(timeout=interval)
                _tick_event.clear()

    _engine_thread = threading.Thread(target=_loop, daemon=True, name="engine-loop")
    _engine_thread.start()


def get_engine_status() -> dict:
    return dict(_engine_status)


def trigger_tick() -> dict:
    """Wake the engine thread to run an immediate tick."""
    _tick_event.set()
    return {"triggered": True, "status": get_engine_status()}
