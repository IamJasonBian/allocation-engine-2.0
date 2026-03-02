"""Background engine thread — runs reconciliation loop alongside Flask."""

import threading
import time
import logging
from datetime import datetime, timezone

log = logging.getLogger(__name__)

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
            from app.brokers import get_broker
            from app.engine import AllocationEngine
            from app.runtime_client import RuntimeClient
            from app.blob_store import sync_to_blob

            config = app.config
            broker = get_broker(config["ENGINE_BROKER"])
            runtime = RuntimeClient(config["RUNTIME_SERVICE_URL"])
            engine = AllocationEngine(
                trader=broker,
                runtime=runtime,
                dry_run=config["DRY_RUN"],
            )

            _engine_status["running"] = True
            _engine_status["dry_run"] = config["DRY_RUN"]
            interval = config["POLL_INTERVAL_SECONDS"]
            is_live = not config["DRY_RUN"]
            blob_interval = 15 * 60  # 15 minutes
            last_blob_sync = 0.0

            log.info("Background engine started (interval=%ds, dry_run=%s, broker=%s)",
                     interval, config["DRY_RUN"], config["ENGINE_BROKER"])

            while True:
                try:
                    if is_live:
                        log.info("Live mode: refreshing broker state")
                    else:
                        # Dry-run mode: run full reconciliation (read-only)
                        engine.tick()

                    _engine_status["last_tick"] = datetime.now(timezone.utc).isoformat()
                    _engine_status["tick_count"] += 1
                    _engine_status["last_error"] = None

                    # Fetch broker state
                    positions = broker.positions()
                    open_orders = broker.open_orders()
                    account = broker.account()

                    # Log portfolio summary
                    log.info(
                        f"[portfolio] Equity: ${account.equity:,.2f} | "
                        f"Cash: ${account.cash:,.2f} | "
                        f"Buying Power: ${account.buying_power:,.2f} | "
                        f"Market Value: ${account.portfolio_value:,.2f}"
                    )

                    # Log open orders
                    if open_orders:
                        for o in open_orders:
                            log.info("[order] %s %s — %s qty=%g limit=$%s status=%s",
                                     o.side, o.symbol, o.order_type,
                                     o.qty, o.limit_price or "MKT", o.status)
                    else:
                        log.info("[order] No open orders")

                    # Sync to Netlify Blobs every 15 minutes (live mode only)
                    now_mono = time.monotonic()
                    if is_live and (now_mono - last_blob_sync) >= blob_interval:
                        try:
                            # Fetch extended data for blob snapshot
                            recent = broker.recent_orders(limit=50)
                            option_pos = broker.option_positions()
                            option_orders = broker.recent_option_orders(limit=20)

                            log.info("[blob] Preparing snapshot: %d positions, %d open orders, "
                                     "%d recent orders, %d option positions, %d option orders",
                                     len(positions), len(open_orders), len(recent),
                                     len(option_pos), len(option_orders))

                            sync_to_blob(
                                positions=positions,
                                open_orders=open_orders,
                                account=account,
                                recent_orders=recent,
                                option_positions=option_pos,
                                option_orders=option_orders,
                            )
                            last_blob_sync = now_mono
                        except Exception:
                            log.exception("Blob sync error")

                except Exception as e:
                    log.exception("Engine tick error")
                    _engine_status["last_error"] = str(e)

                # Wait for interval, but wake early if manual tick requested
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
