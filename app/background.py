"""Worker loop: daily sweeps plus publishing the book (started by gunicorn.conf.py)."""

import os
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


EQUITY_HISTORY_LIMIT = int(os.getenv("EQUITY_HISTORY_LIMIT", "200"))


def _equity_order_history(broker, limit: int = EQUITY_HISTORY_LIMIT) -> list[dict]:
    """Recent filled/completed equity orders, shaped for the Trading DB.

    ``order_history()`` names the average fill price ``price``, but the
    Trading DB reads ``average_price`` and treats ``price`` only as a
    limit-price fallback — so a straight passthrough would null out the fill
    price and P&L would silently fall back to the limit. Copy it across.

    Never raises: a history fetch failing must not cost us the rest of the
    sync.

    Args:
        broker: The active BrokerClient.
        limit: Maximum orders to fetch.

    Returns:
        Order dicts, or an empty list when the broker can't provide them.
    """
    if not hasattr(broker, "order_history"):
        return []
    try:
        orders = broker.order_history(limit=limit) or []
    except Exception:
        log.exception("Failed to fetch equity order history")
        return []

    for o in orders:
        if o.get("average_price") is None and o.get("price") is not None:
            o["average_price"] = o["price"]
    return orders


def book_looks_unreadable(positions, options_positions, account) -> bool:
    """True when the broker read looks failed rather than genuinely flat.

    ``RobinhoodTrader.account()`` degrades an empty profile/portfolio response
    to zeros instead of raising, so a rejected session reads as a real but
    empty account. Publishing that would prune the whole position book
    downstream (``post_positions`` is a whole-book replace), so we treat
    "nothing anywhere, including zero cash" as no data and skip the sync.

    A genuinely emptied account still shows cash or buying power, so it does
    not trip this and will clear normally.

    Args:
        positions: Stock positions from BrokerClient.positions().
        options_positions: Option positions from the broker.
        account: Account summary from BrokerClient.account().

    Returns:
        True when the read should be treated as unavailable.
    """
    if positions or options_positions:
        return False
    fields = ("equity", "cash", "buying_power", "portfolio_value")
    return all(not float((account or {}).get(f) or 0) for f in fields)


def run_engine_loop(app):
    """Run the worker loop forever: read the book, run the daily sweeps, sync.

    Started only on the Render background worker (gunicorn.conf.py post_fork,
    RENDER_SERVICE_TYPE=worker); the API web service never runs it. Each tick reads Robinhood (via the
    auth-service box), runs the equity stop and option take-profit sweeps
    (app/utils_shared.py), and publishes the book to Redis, option history,
    the Trading DB, and S3. It places no other orders.

    Args:
        app: Flask app whose config (app.config.Config) drives the loop.
    """
    from app.brokers import get_broker, clear_broker
    from app.brokers.robinhood_client import RobinhoodTrader, seconds_until_hour_et
    from app.redis_store import sync_to_redis
    from app.s3_store import sync_order_events
    from app.option_history_store import (
        put_position_snapshot as put_option_position_snapshot,
        put_order_snapshot as put_option_order_snapshot,
    )
    from app.slack import notify as slack_notify
    from app.trading_db import post_orders, post_positions
    from app.utils_shared import (
        SweepState, maybe_option_take_profit_sweep, maybe_stop_sweep,
    )

    config = app.config
    broker = None
    interval = config["POLL_INTERVAL_SECONDS"]
    is_live = not config["DRY_RUN"]
    s3_interval = 15 * 60  # 15 minutes
    last_s3_sync = 0.0
    db_sync_interval = config["TRADING_DB_SYNC_SECONDS"]
    last_db_sync = 0.0
    retry_hour = config["RH_RETRY_HOUR_ET"]

    # Daily sweeps: each has its own toggle, auth-service client, and store.
    stop_sweep_state = SweepState()
    opt_tp_sweep_state = SweepState()

    log.info("[engine] worker loop started (interval=%ds, dry_run=%s, broker=%s)",
             interval, config["DRY_RUN"], config["ENGINE_BROKER"])

    while True:
        if broker is None:
            try:
                broker = get_broker(config["ENGINE_BROKER"])
                log.info("Broker initialized successfully")
            except Exception:
                log.exception("Failed to initialize broker")
                time.sleep(interval)
                continue

        try:
            positions = broker.positions()
            open_orders = broker.open_orders()
            account = broker.account()

            # Sweep after positions load so the universe (and live
            # quantities) reflect the real book.
            try:
                maybe_stop_sweep(config, stop_sweep_state, positions)
            except Exception as sweep_err:
                log.exception("[stop-sweeper] sweep failed: %s", sweep_err)

            options_positions = []
            options_open_orders: list[OrderEvent] = []
            if hasattr(broker, "options_positions"):
                try:
                    options_positions = broker.options_positions()
                except Exception:
                    log.exception("Failed to fetch options positions")
            if hasattr(broker, "options_orders"):
                try:
                    for oo in broker.options_orders(limit=200):
                        options_open_orders.append(_option_order_to_event(oo))
                except Exception:
                    log.exception("Failed to fetch options orders")

            try:
                maybe_option_take_profit_sweep(config, opt_tp_sweep_state)
            except Exception as tp_err:
                log.exception("[opt-tp] sweep failed: %s", tp_err)

            equity_events: list[OrderEvent] = [
                _equity_order_to_event(o, is_open=True) for o in open_orders
            ]
            all_order_events = equity_events + options_open_orders

            log.info(
                f"[portfolio] Equity: ${account.get('equity', 0):,.2f} | "
                f"Cash: ${account.get('cash', 0):,.2f} | "
                f"Buying Power: ${account.get('buying_power', 0):,.2f} | "
                f"Market Value: ${account.get('portfolio_value', 0):,.2f}"
            )
            log.info("[options] %d option positions, %d option orders",
                     len(options_positions), len(options_open_orders))

            try:
                sync_to_redis(
                    positions, open_orders, account,
                    live=is_live,
                    options_positions=options_positions,
                    order_events=all_order_events,
                )
            except Exception:
                log.exception("Redis sync error")

            # Option history — unconditional (runs in dry-run too) so the
            # observational record isn't gated by trading mode.
            try:
                now_utc = datetime.now(timezone.utc)
                put_option_position_snapshot(
                    options_positions, ts=now_utc, account=account,
                )
                put_option_order_snapshot(
                    [dict(o) for o in options_open_orders], ts=now_utc,
                )
            except Exception:
                log.exception("Option history sync error")

            now_mono = time.monotonic()

            # --- Trading DB write path (orders + positions) ---
            # Not gated on is_live: a dry-run worker still reads the real
            # book and the dashboard should reflect it.
            if (now_mono - last_db_sync) >= db_sync_interval:
                # Filled equity orders come from order_history(); the open
                # book alone would never grow the record.
                recent_orders = _equity_order_history(broker)
                try:
                    res = post_orders(
                        open_orders=open_orders,
                        recent_orders=recent_orders,
                        recent_option_orders=options_open_orders,
                    )
                    if res:
                        log.info("[trading-db] orders synced: %s", res.get("data"))
                except Exception:
                    log.exception("[trading-db] order sync error")

                # Never publish a book that looks like a failed read —
                # post_positions is a whole-book replace, so that would prune
                # every row the dashboard renders.
                if book_looks_unreadable(positions, options_positions, account):
                    log.warning(
                        "[trading-db] skipping position sync — broker "
                        "returned no positions and a zeroed account "
                        "(treating as a failed read, not a flat book)")
                else:
                    try:
                        res = post_positions(
                            positions=positions,
                            option_positions=options_positions,
                            account=account,
                        )
                        if res:
                            log.info("[trading-db] positions synced: %s",
                                     res.get("data"))
                    except Exception:
                        log.exception("[trading-db] position sync error")

                last_db_sync = now_mono

            if is_live and (now_mono - last_s3_sync) >= s3_interval:
                try:
                    sync_order_events(
                        all_order_events,
                        positions=positions,
                        options_positions=options_positions,
                        account=account,
                    )
                    last_s3_sync = now_mono
                except Exception:
                    log.exception("S3 sync error")

        except Exception:
            log.exception("Engine tick error")

            # If Robinhood is stuck in a device challenge, sleep until the
            # configured retry hour instead of retrying every tick.
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
                time.sleep(wait_secs)
                clear_broker(config["ENGINE_BROKER"])
                broker = None
                continue

        time.sleep(interval)
