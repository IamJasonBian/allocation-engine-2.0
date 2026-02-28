#!/usr/bin/env python3
"""
Backfill Redis with historical stock + option orders from Robinhood.
Run from tunis/ directory:
    python3 scripts/backfill_redis.py
"""

import os
import sys
import json

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv
load_dotenv()

# Redis env vars loaded from .env (REDIS_HOST, REDIS_PASSWORD)

from utils.safe_cash_bot import SafeCashBot
from trading_system.state.redis_store import sync_to_redis

print("Initializing SafeCashBot (will authenticate with Robinhood)...")
bot = SafeCashBot()

print("\nFetching portfolio summary...")
portfolio = bot.get_portfolio_summary()

print("\nFetching recent stock orders (last 30 days)...")
recent_stock_orders = bot.get_recent_orders(days=30)
print(f"  Found {len(recent_stock_orders)} stock orders")
for o in recent_stock_orders:
    print(f"    {o['symbol']} {o['side']} x{o['quantity']} [{o['state']}] {o.get('created_at','')}")

print("\nFetching recent option orders (last 30 days)...")
recent_option_orders = bot.get_recent_option_orders(days=30)
print(f"  Found {len(recent_option_orders)} option orders")
for o in recent_option_orders:
    legs_desc = []
    for leg in o.get('legs', []):
        legs_desc.append(f"{leg.get('side','')} {leg.get('chain_symbol','')} {leg.get('option_type','').upper()} ${leg.get('strike',0)}")
    print(f"    {' / '.join(legs_desc)} [{o['state']}] {o.get('created_at','')}")

print("\nSyncing to Redis...")
sync_to_redis(
    portfolio,
    recent_orders=recent_stock_orders,
    recent_option_orders=recent_option_orders,
    live=True,
)

# Verify
from trading_system.state.redis_store import _get_client
client = _get_client()
stocks = client.hgetall("stocks")
orders = client.hgetall("orders")
meta_orders = json.loads(orders.get("_meta", "{}"))
print(f"\nRedis state:")
print(f"  stocks: {len(stocks) - 1} entries")  # -1 for _meta
print(f"  orders: {len(orders) - 1} entries")
print(f"  orders._meta: {json.dumps(meta_orders, indent=2)}")

# List all orders by type
print(f"\nAll orders in Redis:")
for k, v in sorted(orders.items()):
    if k == "_meta":
        continue
    data = json.loads(v)
    sym = data.get("symbol", "")
    if not sym and data.get("legs"):
        leg = data["legs"][0]
        sym = f"{leg.get('chain_symbol','')} {leg.get('option_type','').upper()} ${leg.get('strike',0)}"
    print(f"  {k}: {sym} {data.get('side','').upper() or data.get('direction','')} [{data['_status']}/{data['_type']}] {data.get('state','')}")

client.close()
print("\nDone!")
