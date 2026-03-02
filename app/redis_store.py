"""Redis sync — writes portfolio positions and orders to Redis hashes.

Two Redis hashes are maintained:
  - stocks: current stock positions keyed by symbol
  - orders: open orders keyed by order_id

Uses REDIS_HOST + REDIS_PASSWORD env vars. Only writes when live=True.
"""

import json
import logging
import os
from datetime import datetime, timezone

log = logging.getLogger(__name__)


def _get_client():
    """Get a Redis client, or None if not configured."""
    try:
        import redis
    except ImportError:
        log.warning("[redis] redis package not installed")
        return None

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
            log.error("[redis] Failed to connect to %s: %s", host, e)
            return None

    url = os.getenv("REDIS_URL")
    if url:
        try:
            import redis as _redis
            return _redis.from_url(url, decode_responses=True)
        except Exception as e:
            log.error("[redis] Failed to connect via URL: %s", e)
            return None

    return None


def sync_to_redis(
    positions: list,
    open_orders: list,
    account,
    live: bool = False,
):
    """Write portfolio positions and orders to Redis.

    Args:
        positions: List of Position from BrokerClient.positions()
        open_orders: List of OpenOrder from BrokerClient.open_orders()
        account: AccountSummary from BrokerClient.account()
        live: Only write when True (skips in dry-run mode)
    """
    if not live:
        return

    client = _get_client()
    if not client:
        return

    ts = datetime.now(timezone.utc).isoformat()

    try:
        pipe = client.pipeline()

        # --- stocks hash: positions keyed by symbol ---
        pipe.delete("stocks")
        for pos in positions:
            entry = {
                "symbol": pos.symbol,
                "name": pos.symbol,
                "type": "stock",
                "quantity": pos.qty,
                "avg_buy_price": pos.avg_entry,
                "current_price": round(pos.market_value / pos.qty, 4) if pos.qty else 0,
                "equity": pos.market_value,
                "profit_loss": pos.unrealized_pl,
                "profit_loss_pct": pos.unrealized_pl_pct * 100,
                "percent_change": round(pos.unrealized_pl_pct * 100, 2),
                "equity_change": pos.unrealized_pl,
            }
            pipe.hset("stocks", pos.symbol, json.dumps(entry))

        pipe.hset("stocks", "_meta", json.dumps({
            "updated_at": ts,
            "num_stocks": len(positions),
            "num_options": 0,
        }))

        # --- orders hash: open orders keyed by order_id ---
        pipe.delete("orders")
        for order in open_orders:
            entry = {
                "order_id": order.id,
                "symbol": order.symbol,
                "side": order.side.upper(),
                "order_type": order.order_type,
                "trigger": "stop" if order.order_type in ("stop", "stop_limit") else "immediate",
                "state": order.status,
                "quantity": order.qty,
                "limit_price": order.limit_price,
                "stop_price": order.stop_price,
                "created_at": ts,
                "updated_at": ts,
                "_status": "open",
                "_type": "stock",
            }
            pipe.hset("orders", order.id, json.dumps(entry))

        pipe.hset("orders", "_meta", json.dumps({
            "updated_at": ts,
            "num_open_stock": len(open_orders),
            "num_open_option": 0,
            "num_historical_stock": 0,
            "num_historical_option": 0,
        }))

        pipe.execute()
        log.info("[redis] Synced: %d positions, %d open orders", len(positions), len(open_orders))

    except Exception as e:
        log.error("[redis] FAILED: %s", e)
    finally:
        try:
            client.close()
        except Exception:
            pass
