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
            from app.brokers import get_broker, clear_broker
            from app.brokers.robinhood_client import RobinhoodTrader, seconds_until_hour_et
            from app.engine import AllocationEngine
            from app.runtime_client import RuntimeClient
            from app.redis_store import sync_to_redis
            from app.blob_store import sync_to_blob
            from app.slack import notify as slack_notify

            config = app.config
            broker = None
            data_broker = None
            engine = None
            runtime = RuntimeClient(config["RUNTIME_SERVICE_URL"])

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

                    # Sync to Redis
                    try:
                        sync_to_redis(positions, open_orders, account, live=is_live)
                    except Exception:
                        log.exception("Redis sync error")

                    now_mono = time.monotonic()
                    if is_live and (now_mono - last_blob_sync) >= blob_interval:
                        try:
                            sync_to_blob(positions, open_orders, account)
                            last_blob_sync = now_mono
                        except Exception:
                            log.exception("Blob sync error")

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
