"""Netlify Blobs sync — uploads state-log snapshots to the 'state-logs' store."""

import json
import logging
import os
from datetime import datetime, timezone

import requests

log = logging.getLogger(__name__)

BLOBS_URL = "https://api.netlify.com/api/v1/blobs"
STORE_NAME = "state-logs"


# ---------------------------------------------------------------------------
# Recommended-action logic for options positions
# ---------------------------------------------------------------------------

def _recommend_option_action(opt: dict) -> dict:
    """Return {"action": "HOLD"|"CLOSE", "reasons": [...]} for an option."""
    reasons: list[str] = []
    dte = opt.get("dte", 999)
    pl_pct = opt.get("unrealized_pl_pct", 0)
    greeks = opt.get("greeks") or {}
    theta = greeks.get("theta")
    iv = greeks.get("iv")
    position_type = opt.get("position_type", "long")

    if dte <= 0:
        return {"action": "CLOSE", "reasons": ["Expired or expiring today"]}

    if position_type == "long":
        if pl_pct >= 1.0:
            reasons.append(f"Up {pl_pct:.0%} — consider taking profit")
        elif pl_pct >= 0.5:
            reasons.append(f"Up {pl_pct:.0%} — consider partial close")
        if dte <= 7 and theta is not None and theta < -0.03:
            reasons.append(f"DTE={dte}, heavy theta decay (${theta:.3f}/day)")
        if dte <= 3:
            reasons.append(f"DTE={dte} — approaching expiration")
    else:  # short
        if pl_pct >= 0.8:
            reasons.append(f"Captured {pl_pct:.0%} of premium")
        if dte <= 3:
            reasons.append(f"DTE={dte} — near expiration, assignment risk")
        if iv is not None and iv > 0.8:
            reasons.append(f"IV={iv:.0%} — elevated volatility")

    if reasons:
        return {"action": "CLOSE", "reasons": reasons}
    return {"action": "HOLD", "reasons": []}


# ---------------------------------------------------------------------------
# Snapshot builders
# ---------------------------------------------------------------------------

def _build_stock_orders(open_orders, order_events):
    """Build stock_orders list from open equity orders."""
    result = []
    machine_idx = 1
    for evt in order_events:
        if evt.get("asset_type") != "equity":
            continue
        result.append({
            "order_id": evt.get("id", ""),
            "symbol": evt.get("symbol", ""),
            "side": evt.get("side", ""),
            "quantity": evt.get("quantity", 0),
            "order_type": evt.get("order_type", ""),
            "limit_price": evt.get("limit_price") or 0,
            "stop_price": evt.get("stop_price") or 0,
            "source": "engine",
            "machine_index": machine_idx,
            "created_at": evt.get("created_at", ""),
        })
        machine_idx += 1
    return result


def _build_options(options_positions):
    """Build options list with greeks and recommended actions."""
    result = []
    for opt in (options_positions or []):
        entry = {
            "chain_symbol": opt.get("chain_symbol", ""),
            "strike": opt.get("strike", 0),
            "option_type": opt.get("option_type", ""),
            "expiration": opt.get("expiration", ""),
            "quantity": opt.get("quantity", 0),
            "position_type": opt.get("position_type", "long"),
            "mark_price": opt.get("mark_price", 0),
            "avg_price": opt.get("avg_price", 0),
            "current_value": opt.get("current_value", 0),
            "unrealized_pl": opt.get("unrealized_pl", 0),
            "unrealized_pl_pct": opt.get("unrealized_pl_pct", 0),
            "dte": opt.get("dte", 0),
            "underlying_price": opt.get("underlying_price") or 0,
            "greeks": opt.get("greeks", {
                "delta": None, "gamma": None,
                "theta": None, "vega": None, "iv": None,
            }),
            "recommended_action": _recommend_option_action(opt),
        }
        result.append(entry)
    return result


def _build_portfolio(positions, account):
    """Build portfolio summary."""
    return {
        "equity": account.get("equity") or account.get("portfolio_value", 0),
        "cash": account.get("cash", 0),
        "buying_power": account.get("buying_power", 0),
        "positions": [
            {
                "symbol": p.get("symbol", ""),
                "quantity": p.get("qty", 0),
                "avg_entry": p.get("avg_entry", 0),
                "current_price": p.get("current_price") or p.get("market_value", 0),
                "market_value": p.get("market_value", 0),
                "unrealized_pl": p.get("unrealized_pl", 0),
                "unrealized_pl_pct": p.get("unrealized_pl_pct", 0),
            }
            for p in (positions or [])
        ],
    }


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def sync_state_log(positions, open_orders, account,
                   options_positions=None, order_events=None):
    """Build and upload a state-log snapshot to Netlify Blobs.

    Writes two keys:
      - 'latest': always-current snapshot for the frontend
      - '{timestamp}': timestamped copy for history
    """
    token = os.getenv("NETLIFY_API_TOKEN")
    site_id = os.getenv("NETLIFY_SITE_ID")
    if not token or not site_id:
        log.debug("[state-log] NETLIFY_API_TOKEN or NETLIFY_SITE_ID not set, skipping")
        return

    if options_positions is None:
        options_positions = []
    if order_events is None:
        order_events = []

    now = datetime.now(timezone.utc)
    ts_key = now.strftime("%Y-%m-%dT%H-%M-%S")

    stock_orders = _build_stock_orders(open_orders, order_events)
    options = _build_options(options_positions)
    portfolio = _build_portfolio(positions, account)

    snapshot = {
        "snapshot_key": ts_key,
        "timestamp": now.isoformat(),
        "stock_orders": stock_orders,
        "stock_order_count": len(stock_orders),
        "machine_order_count": sum(1 for o in stock_orders if o.get("source") == "engine"),
        "options": options,
        "options_count": len(options),
        "portfolio": portfolio,
    }

    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    }
    payload = json.dumps(snapshot)

    for blob_key in ("latest", ts_key):
        url = f"{BLOBS_URL}/{site_id}/{STORE_NAME}/{blob_key}"
        try:
            resp = requests.put(url, headers=headers, data=payload, timeout=15)
            resp.raise_for_status()
            log.info("[state-log] PUT %s/%s -> %s (%d bytes)",
                     STORE_NAME, blob_key, resp.status_code, len(payload))
        except Exception:
            log.exception("[state-log] Failed to upload %s/%s", STORE_NAME, blob_key)
