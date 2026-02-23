#!/usr/bin/env python3
"""Allocation Engine 2.0 — Alpaca-backed trade execution.

Reads desired orders from the allocation-runtime-service, reconciles
against live Alpaca state, and submits the delta.
"""

import argparse
import logging
import sys
import time

from config import POLL_INTERVAL_SECONDS, DRY_RUN
from engine import AllocationEngine

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


def run_loop(engine: AllocationEngine, interval: int):
    log.info("Starting engine loop (interval=%ds, dry_run=%s)", interval, engine.dry_run)
    while True:
        run_once(engine)
        time.sleep(interval)


def status(engine: AllocationEngine):
    """Print current Alpaca account + runtime service state."""
    acct = engine.trader.account()
    positions = engine.trader.positions()
    alpaca_orders = engine.trader.open_orders()
    runtime_orders = engine.runtime.orders()

    print("\n=== Alpaca Account ===")
    for k, v in acct.items():
        print(f"  {k}: {v}")

    print(f"\n=== Alpaca Positions ({len(positions)}) ===")
    for p in positions:
        print(f"  {p['symbol']}: {p['qty']} shares, MV=${p['market_value']:.2f}, "
              f"PnL=${p['unrealized_pl']:.2f} ({p['unrealized_pl_pct']:.2%})")

    print(f"\n=== Alpaca Open Orders ({len(alpaca_orders)}) ===")
    for o in alpaca_orders:
        print(f"  {o['id'][:8]}  {o['side']} {o['qty']} {o['symbol']} "
              f"{o['type']} @ {o['limit_price'] or 'MKT'}")

    rt_stock = runtime_orders.get("stock_orders", [])
    print(f"\n=== Runtime Service Desired Orders ({len(rt_stock)}) ===")
    for o in rt_stock:
        print(f"  {o['side']} {o['quantity']} {o['symbol']} "
              f"{o.get('order_type', 'market')} @ {o.get('limit_price', 'MKT')}")


def main():
    parser = argparse.ArgumentParser(description="Allocation Engine 2.0")
    sub = parser.add_subparsers(dest="command")

    sub.add_parser("run", help="Run continuous reconciliation loop")
    sub.add_parser("once", help="Run a single reconciliation tick")
    sub.add_parser("status", help="Print Alpaca + runtime service status")

    args = parser.parse_args()
    engine = AllocationEngine(dry_run=DRY_RUN)

    if args.command == "run":
        run_loop(engine, POLL_INTERVAL_SECONDS)
    elif args.command == "once":
        run_once(engine)
    elif args.command == "status":
        status(engine)
    else:
        parser.print_help()
        sys.exit(1)


if __name__ == "__main__":
    main()
