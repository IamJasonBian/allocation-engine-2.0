"""
Redis Store
Writes portfolio data to two Redis stores:
  - stocks: current stock positions keyed by symbol (+ options under OPT: prefix)
  - orders: open + historical orders keyed by order_id (stocks + options)

Uses REDIS_HOST + REDIS_PASSWORD env vars (from Netlify).
Fallback: REDIS_URL if set. Only writes in live mode.
"""

import json
import os
from datetime import datetime


def _get_client():
    """Get a Redis client, or None if not configured."""
    try:
        import redis
    except ImportError:
        print("  [redis] redis package not installed")
        return None

    # Prefer REDIS_HOST + REDIS_PASSWORD (Netlify env vars)
    host = os.getenv("REDIS_HOST")
    password = os.getenv("REDIS_PASSWORD")
    if host:
        port = 6379
        if ":" in host:
            host, port_str = host.rsplit(":", 1)
            try:
                port = int(port_str)
            except ValueError:
                pass
        try:
            return redis.Redis(
                host=host, port=port, password=password,
                decode_responses=True,
            )
        except Exception as e:
            print(f"  [redis] Failed to connect to {host}: {e}")
            return None

    # Fallback to REDIS_URL
    url = os.getenv("REDIS_URL")
    if url:
        try:
            return redis.from_url(url, decode_responses=True)
        except Exception as e:
            print(f"  [redis] Failed to connect via URL: {e}")
            return None

    return None


def sync_to_redis(portfolio_data, recent_orders=None, recent_option_orders=None, live=False):
    """Write portfolio data to Redis.

    Args:
        portfolio_data: Dict from SafeCashBot.get_portfolio_summary()
        recent_orders: List of recently filled/cancelled stock orders
        recent_option_orders: List of recently filled/cancelled option orders
        live: Only write when True
    """
    if not live or not portfolio_data:
        return

    client = _get_client()
    if not client:
        return

    ts = datetime.now().isoformat()

    try:
        pipe = client.pipeline()

        # --- Store 1: stocks (Hash keyed by symbol) ---
        positions = portfolio_data.get("positions", [])
        options = portfolio_data.get("options", [])

        pipe.delete("stocks")
        for pos in positions:
            symbol = pos.get("symbol")
            if symbol:
                pipe.hset("stocks", symbol, json.dumps(pos))

        # Options stored under their compound key
        for opt in options:
            symbol = opt.get("chain_symbol", "UNK")
            otype = (opt.get("option_type") or "unk").upper()
            strike = opt.get("strike", 0)
            exp = opt.get("expiration", "N/A")
            key = f"{symbol}_{otype}_{strike}_{exp}"
            pipe.hset("stocks", f"OPT:{key}", json.dumps(opt))

        # --- Store 2: orders (Hash keyed by order_id) ---
        open_orders = portfolio_data.get("open_orders", [])
        open_option_orders = portfolio_data.get("open_option_orders", [])

        pipe.delete("orders")

        # Open stock orders
        for order in open_orders:
            oid = order.get("order_id", "unknown")
            order["_status"] = "open"
            order["_type"] = "stock"
            pipe.hset("orders", oid, json.dumps(order))

        # Open option orders
        for order in open_option_orders:
            oid = order.get("order_id", "unknown")
            order["_status"] = "open"
            order["_type"] = "option"
            pipe.hset("orders", oid, json.dumps(order))

        # Historical stock orders (filled/cancelled)
        if recent_orders:
            for order in recent_orders:
                oid = order.get("order_id") or order.get("id", "unknown")
                order["_status"] = "historical"
                order["_type"] = "stock"
                pipe.hset("orders", oid, json.dumps(order))

        # Historical option orders (filled/cancelled)
        if recent_option_orders:
            for order in recent_option_orders:
                oid = order.get("order_id") or order.get("id", "unknown")
                order["_status"] = "historical"
                order["_type"] = "option"
                pipe.hset("orders", oid, json.dumps(order))

        # Metadata
        hist_stock = len(recent_orders) if recent_orders else 0
        hist_option = len(recent_option_orders) if recent_option_orders else 0
        pipe.hset("stocks", "_meta", json.dumps({
            "updated_at": ts,
            "num_stocks": len(positions),
            "num_options": len(options),
        }))
        pipe.hset("orders", "_meta", json.dumps({
            "updated_at": ts,
            "num_open_stock": len(open_orders),
            "num_open_option": len(open_option_orders),
            "num_historical_stock": hist_stock,
            "num_historical_option": hist_option,
        }))

        pipe.execute()

        total_open = len(open_orders) + len(open_option_orders)
        total_hist = hist_stock + hist_option
        print(f"  [redis] Synced: {len(positions)} stocks, {len(options)} options, "
              f"{total_open} open orders, {total_hist} historical ({hist_stock} stock, {hist_option} option)")

    except Exception as e:
        print(f"  [redis] FAILED: {e}")
    finally:
        try:
            client.close()
        except Exception:
            pass
