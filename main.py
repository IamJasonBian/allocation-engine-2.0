#!/usr/bin/env python3
"""Allocation Engine 2.0 — CLI entry point for local development.

For production, use: gunicorn app.wsgi:application
"""

import argparse
import logging
import os
import sys
import time

from app import create_app
from app.config import Config
from app.engine import AllocationEngine
from app.brokers import get_broker
from app.heartbeat import DEFAULT_PATH as HEARTBEAT_PATH, write_heartbeat
from app.risk import RiskSubject, SlackAlertObserver, RebalancerObserver
from app.runtime_client import RuntimeClient

# Cap the exponential backoff so a long gateway outage still retries ~every 5 min.
MAX_BACKOFF_SECONDS = 300

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-8s %(name)s  %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("main")


def run_once(engine: AllocationEngine):
    try:
        engine.tick()
    except Exception:
        log.exception("Error during tick")


def tick_and_report(engine: AllocationEngine, state: dict, interval: int,
                    heartbeat_path: str = HEARTBEAT_PATH) -> int:
    """Run one resilient tick: update state, write a heartbeat, return next sleep.

    A ConnectionError means the IBKR gateway is unreachable — expected during
    Sunday maintenance and the weekly re-auth — so it is retryable, never fatal.
    Consecutive failures back off exponentially (capped) so an unattended box
    keeps trying without hammering a down gateway. Any success resets the streak.

    Args:
        engine: the allocation engine to tick.
        state: mutable dict carrying ``tick_count`` and ``consecutive_errors``.
        interval: normal poll interval in seconds.
        heartbeat_path: where to write the heartbeat JSON.

    Returns:
        Seconds to sleep before the next tick.
    """
    try:
        engine.tick()
        state["tick_count"] += 1
        state["consecutive_errors"] = 0
        status_, last_error = "ok", None
    except ConnectionError as e:
        state["consecutive_errors"] += 1
        status_, last_error = "error", f"ConnectionError: {e}"
        log.warning("tick failed — gateway unreachable (attempt %d): %s",
                    state["consecutive_errors"], e)
    except Exception as e:  # noqa: BLE001 — the loop must never die on a tick
        state["consecutive_errors"] += 1
        status_, last_error = "error", f"{type(e).__name__}: {e}"
        log.exception("tick failed")

    try:
        write_heartbeat(heartbeat_path, status=status_,
                        tick_count=state["tick_count"],
                        consecutive_errors=state["consecutive_errors"],
                        last_error=last_error)
    except Exception:  # noqa: BLE001 — heartbeat is best-effort, never fatal
        log.exception("heartbeat write failed")

    if state["consecutive_errors"] == 0:
        return interval
    # exponential backoff on the failure streak, capped
    return min(interval * 2 ** min(state["consecutive_errors"], 6),
               MAX_BACKOFF_SECONDS)


def run_loop(engine: AllocationEngine, interval: int):
    log.info("Starting engine loop (interval=%ds, dry_run=%s)", interval, engine.dry_run)
    state = {"tick_count": 0, "consecutive_errors": 0}
    while True:
        sleep_s = tick_and_report(engine, state, interval)
        time.sleep(sleep_s)


def status(engine: AllocationEngine, broker_name: str):
    """Print broker account + runtime service state."""
    acct = engine.trader.account()
    positions = engine.trader.positions()
    broker_orders = engine.trader.open_orders()
    runtime_orders = engine.runtime.orders()

    print(f"\n=== {broker_name.title()} Account ===")
    for k, v in acct.items():
        print(f"  {k}: {v}")

    print(f"\n=== {broker_name.title()} Positions ({len(positions)}) ===")
    for p in positions:
        print(f"  {p['symbol']}: {p['qty']} shares, MV=${p['market_value']:.2f}, "
              f"PnL=${p['unrealized_pl']:.2f} ({p['unrealized_pl_pct']:.2%})")

    print(f"\n=== {broker_name.title()} Open Orders ({len(broker_orders)}) ===")
    for o in broker_orders:
        oid = o['id'][:8] if o.get('id') else '?'
        print(f"  {oid}  {o['side']} {o['qty']} {o['symbol']} "
              f"{o['type']} @ {o['limit_price'] or 'MKT'}")

    rt_stock = runtime_orders.get("stock_orders", [])
    print(f"\n=== Runtime Service Desired Orders ({len(rt_stock)}) ===")
    for o in rt_stock:
        print(f"  {o['side']} {o['quantity']} {o['symbol']} "
              f"{o.get('order_type', 'market')} @ {o.get('limit_price', 'MKT')}")

    # -- Market data & drift --
    try:
        mkt = engine.runtime.market_data()
        tickers = mkt.get("tickers", {})
        print(f"\n=== Market Data ({len(tickers)} tickers) ===")
        for sym, m in sorted(tickers.items()):
            drift = m.get("drift_pct", 0)
            flag = " ** DRIFT" if abs(drift) >= 0.08 else ""
            price = m.get("price")
            price_str = f"${price:,.2f}" if price is not None else "n/a"
            print(f"  {sym:6s}  {price_str:>12s}  "
                  f"target={m.get('target_pct', 0):6.2%}  "
                  f"actual={m.get('actual_pct', 0):6.2%}  "
                  f"drift={drift:+6.2%}{flag}")
    except Exception as e:
        print(f"\n=== Market Data (unavailable: {e}) ===")


def serve():
    """Run the Flask development server (use gunicorn for production)."""
    app = create_app()
    port = int(os.getenv("PORT", "10000"))
    app.run(host="0.0.0.0", port=port, debug=Config.DEBUG)


def main():
    parser = argparse.ArgumentParser(description="Allocation Engine 2.0")
    parser.add_argument("--broker", default=Config.ENGINE_BROKER,
                        choices=["alpaca", "robinhood"],
                        help="Broker to use (default: ENGINE_BROKER env var)")
    sub = parser.add_subparsers(dest="command")

    sub.add_parser("run", help="Run continuous reconciliation loop")
    sub.add_parser("once", help="Run a single reconciliation tick")
    sub.add_parser("status", help="Print broker + runtime service status")
    sub.add_parser("serve", help="Run Flask dev server (use gunicorn for prod)")

    args = parser.parse_args()

    if args.command == "serve":
        serve()
        return

    # For CLI commands, create app context to initialize brokers
    app = create_app()
    with app.app_context():
        broker = get_broker(args.broker)
        runtime = RuntimeClient(Config.RUNTIME_SERVICE_URL)

        # Set up risk event bus with observers
        risk_subject = RiskSubject()
        if os.getenv("TELEGRAM_BOT_TOKEN") and os.getenv("TELEGRAM_CHAT_ID"):
            risk_subject.attach(SlackAlertObserver())
        rebalancer = RebalancerObserver()

        engine = AllocationEngine(
            trader=broker, runtime=runtime,
            dry_run=Config.DRY_RUN, risk_subject=risk_subject,
        )
        engine.register_rebalancer(rebalancer)

        if args.command == "run":
            run_loop(engine, Config.POLL_INTERVAL_SECONDS)
        elif args.command == "once":
            run_once(engine)
        elif args.command == "status":
            status(engine, args.broker)
        else:
            parser.print_help()
            sys.exit(1)


if __name__ == "__main__":
    main()
