#!/usr/bin/env python3
"""Allocation Engine 2.0 — Alpaca-backed trade execution.

Reads desired orders from the allocation-runtime-service, reconciles
against live Alpaca state, submits via OTO pairing, and uploads
gamma-tagged snapshots to the blob store.
"""

import argparse
import logging
import sys
import time

from config import POLL_INTERVAL_SECONDS, DRY_RUN
from engine import AllocationEngine
from order_book import print_audit

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
    """Print Alpaca account, positions, open orders, OTO state, and runtime diff."""
    acct = engine.trader.account()
    positions = engine.trader.positions()
    alpaca_orders = engine.trader.open_orders()
    runtime_orders = engine.runtime.orders()

    print("\n=== Alpaca Account ===")
    for k, v in acct.items():
        print(f"  {k}: {v}")

    print(f"\n=== Alpaca Positions ({len(positions)}) ===")
    if not positions:
        print("  (none)")
    for p in positions:
        print(f"  {p['symbol']}: {p['qty']} shares, MV=${p['market_value']:.2f}, "
              f"PnL=${p['unrealized_pl']:.2f} ({p['unrealized_pl_pct']:.2%})")

    # Group orders to show OTO legs
    print(f"\n=== Alpaca Open Orders ({len(alpaca_orders)}) ===")
    for o in alpaca_orders:
        tag = ""
        # check if this is tracked in the order book
        book_rec = engine.book.orders.get(o["id"])
        if book_rec and book_rec.get("order_type") == "oto":
            tag = f" [OTO -> BUY @ {book_rec['oto_buy_price']}]"
        print(f"  {o['id'][:8]}  {o['side']} {o['qty']} {o['symbol']} "
              f"{o['type']} @ {o['limit_price'] or 'MKT'}{tag}")

    rt_stock = runtime_orders.get("stock_orders", [])
    print(f"\n=== Runtime Service Desired Orders ({len(rt_stock)}) ===")
    for o in rt_stock:
        print(f"  {o['side']} {o['quantity']} {o['symbol']} "
              f"{o.get('order_type', 'market')} @ {o.get('limit_price', 'MKT')}")

    # Order book summary
    summary = engine.book.summary()
    print(f"\n=== Order Book (this session) ===")
    print(f"  Tracked: {summary['total_tracked']}, "
          f"Open: {summary['open']}, "
          f"Resolved: {summary['resolved']}, "
          f"Fills: {summary['fills_this_session']}")

    # Diff: what's desired but not on Alpaca
    alpaca_set = {(o["symbol"], o["side"], o["qty"], o["limit_price"])
                  for o in alpaca_orders}
    missing = []
    for o in rt_stock:
        key = (o["symbol"], o["side"].lower(), float(o["quantity"]), o.get("limit_price"))
        if key not in alpaca_set:
            missing.append(o)
    if missing:
        print(f"\n=== Desired But Not On Alpaca ({len(missing)}) ===")
        for o in missing:
            print(f"  {o['side']} {o['quantity']} {o['symbol']} @ {o.get('limit_price', 'MKT')}")
        print("  (BUY orders are held back — submitted as OTO legs on sell fill)")


def audit(engine: AllocationEngine):
    """Run and print the order coverage audit."""
    report = engine.get_audit()
    print_audit(report)


def replace(engine: AllocationEngine):
    """Atomic cancel-all + resubmit from runtime service."""
    log.info("Running atomic order replacement")
    execution_log = engine.replace_all_orders()
    log.info("Replacement complete: %d actions", len(execution_log))


def main():
    parser = argparse.ArgumentParser(description="Allocation Engine 2.0")
    sub = parser.add_subparsers(dest="command")

    sub.add_parser("run", help="Run continuous reconciliation loop")
    sub.add_parser("once", help="Run a single reconciliation tick")
    sub.add_parser("status", help="Print Alpaca + runtime service status")
    sub.add_parser("audit", help="Run order coverage audit")
    sub.add_parser("replace", help="Atomic cancel-all + resubmit")

    hedge_parser = sub.add_parser("hedge", help="BTC protective put analysis")
    hedge_parser.add_argument("--shares", type=int, default=None,
                              help="Override share count (default: read from position)")
    hedge_parser.add_argument("--lookback", type=int, default=90,
                              help="Days of history to fetch (default: 90)")

    args = parser.parse_args()
    engine = AllocationEngine(dry_run=DRY_RUN)

    if args.command == "run":
        run_loop(engine, POLL_INTERVAL_SECONDS)
    elif args.command == "once":
        run_once(engine)
    elif args.command == "status":
        status(engine)
    elif args.command == "audit":
        audit(engine)
    elif args.command == "replace":
        replace(engine)
    elif args.command == "hedge":
        from hedging import fetch_btc_bars, print_hedge_report
        log.info("Fetching BTC bars (lookback=%dd)", args.lookback)
        bars = fetch_btc_bars(lookback_days=args.lookback)
        positions = engine.trader.positions()
        print_hedge_report(bars, positions, shares_override=args.shares)
    else:
        parser.print_help()
        sys.exit(1)


if __name__ == "__main__":
    main()
