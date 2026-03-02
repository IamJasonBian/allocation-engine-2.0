"""Netlify Blobs sync — uploads order book snapshots to the 'order-book' store.

Writes snapshots in the schema expected by the allocation-manager frontend
(OrderBookSnapshot type in robinhoodService.ts).
"""

import json
import logging
import os
from datetime import datetime, timezone

import requests

log = logging.getLogger(__name__)

BLOBS_URL = "https://api.netlify.com/api/v1/blobs"
STORE_NAME = "order-book"


def sync_to_blob(
    positions,
    open_orders,
    account,
    recent_orders=None,
    option_positions=None,
    option_orders=None,
):
    """Upload current portfolio state to Netlify Blobs.

    Writes two keys:
      - 'latest': always-current snapshot for the frontend
      - '{timestamp}': timestamped copy for history
    """
    token = os.getenv("NETLIFY_API_TOKEN")
    site_id = os.getenv("NETLIFY_SITE_ID")
    if not token or not site_id:
        log.debug("[blob] NETLIFY_API_TOKEN or NETLIFY_SITE_ID not set, skipping")
        return

    recent_orders = recent_orders or []
    option_positions = option_positions or []
    option_orders = option_orders or []

    now = datetime.now(timezone.utc)
    ts = now.isoformat()

    # Map positions to the frontend SnapshotPosition schema
    snap_positions = [
        {
            "symbol": p.symbol,
            "quantity": p.qty,
            "avg_buy_price": p.avg_entry,
            "current_price": round(p.market_value / p.qty, 4) if p.qty else 0,
            "equity": p.market_value,
            "profit_loss": p.unrealized_pl,
            "profit_loss_pct": p.unrealized_pl_pct,
        }
        for p in positions
    ]

    # Map open orders to the frontend SnapshotOrder schema
    snap_open_orders = [
        {
            "order_id": o.id,
            "symbol": o.symbol,
            "side": o.side.upper(),
            "order_type": o.order_type,
            "trigger": "immediate",
            "state": o.status,
            "quantity": o.qty,
            "limit_price": o.limit_price or 0,
            "stop_price": o.stop_price,
            "created_at": ts,
            "updated_at": ts,
        }
        for o in open_orders
    ]

    # Map recent filled/cancelled orders to SnapshotOrder schema
    snap_recent_orders = [
        {
            "order_id": o.id,
            "symbol": o.symbol,
            "side": o.side.upper(),
            "order_type": o.order_type,
            "trigger": "immediate",
            "state": o.status,
            "quantity": o.qty,
            "limit_price": o.limit_price or 0,
            "stop_price": o.stop_price,
            "average_price": o.average_price,
            "filled_quantity": o.filled_qty,
            "created_at": o.created_at,
            "updated_at": o.updated_at,
        }
        for o in recent_orders
    ]

    # Map option positions to the frontend OptionPosition schema
    snap_options = [
        {
            "chain_symbol": op.chain_symbol,
            "option_type": op.option_type,
            "strike": op.strike,
            "expiration": op.expiration,
            "dte": _days_until(op.expiration),
            "quantity": op.quantity,
            "position_type": op.position_type,
            "avg_price": op.avg_price,
            "mark_price": op.mark_price,
            "multiplier": op.multiplier,
            "cost_basis": op.cost_basis,
            "current_value": op.current_value,
            "unrealized_pl": op.unrealized_pl,
            "unrealized_pl_pct": op.unrealized_pl_pct,
            "underlying_price": op.underlying_price,
            "break_even": op.break_even,
            "greeks": {
                "delta": op.delta,
                "gamma": op.gamma,
                "theta": op.theta,
                "vega": op.vega,
                "rho": op.rho,
                "iv": op.iv,
            },
            "expected_pl": {
                "-5%": round(op.delta * op.underlying_price * -0.05 * op.multiplier * op.quantity, 2) if op.underlying_price else 0,
                "-1%": round(op.delta * op.underlying_price * -0.01 * op.multiplier * op.quantity, 2) if op.underlying_price else 0,
                "+1%": round(op.delta * op.underlying_price * 0.01 * op.multiplier * op.quantity, 2) if op.underlying_price else 0,
                "+5%": round(op.delta * op.underlying_price * 0.05 * op.multiplier * op.quantity, 2) if op.underlying_price else 0,
                "theta_daily": round(op.theta * op.multiplier * op.quantity, 2),
            },
            "chance_of_profit": op.chance_of_profit,
            "recommended_action": {"action": "hold", "reasons": []},
            "btc_correlation": 0.0,
        }
        for op in option_positions
    ]

    # Map option orders to the frontend SnapshotOptionOrder schema
    snap_option_orders = [
        {
            "order_id": oo.id,
            "state": oo.state,
            "quantity": oo.quantity,
            "price": oo.price,
            "premium": oo.premium,
            "processed_premium": oo.premium,
            "direction": oo.direction,
            "order_type": oo.order_type,
            "trigger": oo.trigger,
            "time_in_force": oo.time_in_force,
            "opening_strategy": oo.opening_strategy,
            "created_at": oo.created_at,
            "updated_at": oo.updated_at,
            "legs": [
                {
                    "side": leg.get("side", ""),
                    "position_effect": leg.get("position_effect", ""),
                    "quantity": leg.get("quantity", 0),
                    "strike": leg.get("strike", 0),
                    "expiration": leg.get("expiration", ""),
                    "option_type": leg.get("option_type", ""),
                    "chain_symbol": leg.get("chain_symbol", ""),
                }
                for leg in oo.legs
            ],
        }
        for oo in option_orders
    ]

    snapshot = {
        "timestamp": ts,
        "portfolio": {
            "cash": {
                "cash": account.cash,
                "buying_power": account.buying_power,
                "cash_available_for_withdrawal": account.cash,
                "tradeable_cash": account.cash,
            },
            "equity": account.equity,
            "market_value": account.portfolio_value,
            "positions": snap_positions,
            "open_orders": snap_open_orders,
            "options": snap_options,
            "open_option_orders": snap_option_orders,
        },
        "order_book": snap_open_orders,
        "recent_orders": snap_recent_orders,
        "recent_option_orders": snap_option_orders,
    }

    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    }
    payload = json.dumps(snapshot)

    for blob_key in ("latest", now.strftime("%Y-%m-%dT%H-%M-%S")):
        url = f"{BLOBS_URL}/{site_id}/{STORE_NAME}/{blob_key}"
        try:
            resp = requests.put(url, headers=headers, data=payload, timeout=15)
            resp.raise_for_status()
            log.info("[blob] PUT %s/%s -> %s (%d bytes)",
                     STORE_NAME, blob_key, resp.status_code, len(payload))
        except Exception:
            log.exception("[blob] Failed to upload %s/%s", STORE_NAME, blob_key)


def _days_until(date_str: str) -> int:
    """Calculate days until expiration from a YYYY-MM-DD string."""
    if not date_str:
        return 0
    try:
        exp = datetime.strptime(date_str, "%Y-%m-%d").date()
        return max(0, (exp - datetime.now(timezone.utc).date()).days)
    except ValueError:
        return 0
