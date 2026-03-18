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


def sync_to_redis(positions, open_orders, account, live=False,
                  options_positions=None, order_events=None):
    """Write portfolio positions and orders to Redis.

    Args:
        positions: List of position dicts from BrokerClient.positions()
        open_orders: List of order dicts from BrokerClient.open_orders()
        account: Account summary dict from BrokerClient.account()
        live: Only write when True (skips in dry-run mode)
        options_positions: List of options position dicts (optional)
        order_events: List of unified OrderEvent dicts — both equity and
                      option orders (optional; falls back to open_orders)
    """
    if not live:
        return

    client = _get_client()
    if not client:
        return

    if options_positions is None:
        options_positions = []

    ts = datetime.now(timezone.utc).isoformat()

    # --- Split order events by asset type and state ---
    open_states = {"queued", "unconfirmed", "confirmed", "partially_filled", "pending"}

    if order_events:
        equity_open = [e for e in order_events
                       if e.get("asset_type") == "equity"
                       and e.get("state") in open_states]
        option_open = [e for e in order_events
                       if e.get("asset_type") == "option"
                       and e.get("state") in open_states]
        equity_hist = [e for e in order_events
                       if e.get("asset_type") == "equity"
                       and e.get("state") not in open_states]
        option_hist = [e for e in order_events
                       if e.get("asset_type") == "option"
                       and e.get("state") not in open_states]
    else:
        # Fallback: wrap raw open_orders as equity events
        equity_open = []
        for order in open_orders:
            equity_open.append({
                "id": order.get("id", "unknown"),
                "symbol": order.get("symbol", ""),
                "side": order.get("side", "").upper(),
                "order_type": order.get("type", "market"),
                "asset_type": "equity",
                "trigger": "stop" if order.get("type") in ("stop", "stop_limit") else "immediate",
                "state": order.get("status", "unknown"),
                "quantity": order.get("qty", 0),
                "limit_price": order.get("limit_price"),
                "stop_price": order.get("stop_price"),
            })
        option_open = []
        equity_hist = []
        option_hist = []

    try:
        pipe = client.pipeline()

        # --- stocks hash: positions keyed by symbol ---
        pipe.delete("stocks")
        for pos in positions:
            symbol = pos.get("symbol")
            if not symbol:
                continue
            qty = pos["qty"]
            entry = {
                "symbol": symbol,
                "name": symbol,
                "type": "stock",
                "quantity": qty,
                "avg_buy_price": pos.get("avg_entry", 0),
                "current_price": round(pos["market_value"] / qty, 4) if qty else 0,
                "equity": pos["market_value"],
                "profit_loss": pos.get("unrealized_pl", 0),
                "profit_loss_pct": pos.get("unrealized_pl_pct", 0) * 100,
                "percent_change": round(pos.get("unrealized_pl_pct", 0) * 100, 2),
                "equity_change": pos.get("unrealized_pl", 0),
            }
            pipe.hset("stocks", symbol, json.dumps(entry))

        # Options positions in the same hash with "OPT:" prefix
        for opt in options_positions:
            sym = opt.get("chain_symbol", "")
            strike = opt.get("strike", 0)
            exp = opt.get("expiration", "")
            otype = opt.get("option_type", "call")[0].upper()
            key = f"OPT:{sym}:{exp}:{strike}{otype}"
            entry = {
                "symbol": sym,
                "name": f"{sym} {exp} ${strike} {otype}",
                "type": "option",
                "quantity": opt.get("quantity", 0),
                "avg_buy_price": opt.get("avg_price", 0),
                "current_price": opt.get("mark_price", 0),
                "equity": opt.get("current_value", 0),
                "profit_loss": opt.get("unrealized_pl", 0),
                "profit_loss_pct": opt.get("unrealized_pl_pct", 0) * 100,
                "percent_change": round(opt.get("unrealized_pl_pct", 0) * 100, 2),
                "equity_change": opt.get("unrealized_pl", 0),
                "strike": strike,
                "expiration": exp,
                "option_type": opt.get("option_type", ""),
                "dte": opt.get("dte", 0),
                "multiplier": opt.get("multiplier", 100),
                "cost_basis": opt.get("cost_basis", 0),
            }
            pipe.hset("stocks", key, json.dumps(entry))

        pipe.hset("stocks", "_meta", json.dumps({
            "updated_at": ts,
            "num_stocks": len(positions),
            "num_options": len(options_positions),
        }))

        # --- orders hash: all order events keyed by id ---
        pipe.delete("orders")

        all_events = equity_open + option_open + equity_hist + option_hist
        for evt in all_events:
            oid = evt.get("id", "unknown")
            state = evt.get("state", "unknown")
            entry = {
                "order_id": oid,
                "symbol": evt.get("symbol", ""),
                "side": evt.get("side", "").upper(),
                "order_type": evt.get("order_type", "market"),
                "asset_type": evt.get("asset_type", "equity"),
                "trigger": evt.get("trigger", "immediate"),
                "state": state,
                "quantity": evt.get("quantity", 0),
                "filled_quantity": evt.get("filled_quantity", 0),
                "limit_price": evt.get("limit_price"),
                "stop_price": evt.get("stop_price"),
                "price": evt.get("price"),
                "created_at": evt.get("created_at", ts),
                "updated_at": evt.get("updated_at", ts),
                "_status": "open" if state in open_states else "historical",
                "_type": evt.get("asset_type", "equity"),
            }
            # Include option-specific fields
            if evt.get("asset_type") == "option":
                entry["legs"] = evt.get("legs")
                entry["direction"] = evt.get("direction")
                entry["opening_strategy"] = evt.get("opening_strategy")
                entry["premium"] = evt.get("premium")
                entry["processed_premium"] = evt.get("processed_premium")

            pipe.hset("orders", oid, json.dumps(entry))

        pipe.hset("orders", "_meta", json.dumps({
            "updated_at": ts,
            "num_open_stock": len(equity_open),
            "num_open_option": len(option_open),
            "num_historical_stock": len(equity_hist),
            "num_historical_option": len(option_hist),
        }))

        pipe.execute()
        log.info("[redis] Synced: %d stock positions, %d option positions, "
                 "%d equity orders, %d option orders",
                 len(positions), len(options_positions),
                 len(equity_open) + len(equity_hist),
                 len(option_open) + len(option_hist))

    except Exception as e:
        log.error("[redis] FAILED: %s", e)
    finally:
        try:
            client.close()
        except Exception:
            pass
