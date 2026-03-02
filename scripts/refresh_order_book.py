#!/usr/bin/env python3
"""
Robinhood Order Book Refresher

Queries Robinhood for current positions, open orders, and account info,
then uploads a snapshot to the Netlify Blobs 'order-book' store.

Env vars required:
  RH_USER          - Robinhood email
  RH_PASS          - Robinhood password
  RH_TOTP_SECRET   - Base32 TOTP secret for MFA
  RH_DEVICE_TOKEN  - (optional) fixed device UUID
  NETLIFY_API_TOKEN
  NETLIFY_SITE_ID
"""

import json
import os
import sys
from datetime import datetime, timezone

import pyotp
import requests
import robin_stocks.robinhood as rh

BLOBS_URL = "https://api.netlify.com/api/v1/blobs"
STORE_NAME = "order-book"

# Cache instrument URL -> symbol
_instrument_cache: dict[str, str] = {}


def _symbol_from_instrument(instrument_url: str) -> str:
    if instrument_url in _instrument_cache:
        return _instrument_cache[instrument_url]
    try:
        data = rh.stocks.get_instrument_by_url(instrument_url)
        symbol = data.get("symbol", "UNKNOWN") if data else "UNKNOWN"
    except Exception:
        symbol = "UNKNOWN"
    _instrument_cache[instrument_url] = symbol
    return symbol


def login():
    email = os.getenv("RH_USER")
    password = os.getenv("RH_PASS")
    totp_secret = os.getenv("RH_TOTP_SECRET", "")
    device_token = os.getenv("RH_DEVICE_TOKEN", "")

    if not email or not password:
        print("ERROR: RH_USER and RH_PASS required")
        sys.exit(1)

    kwargs = {"store_session": False}
    if totp_secret:
        kwargs["mfa_code"] = pyotp.TOTP(totp_secret).now()
    if device_token:
        kwargs["device_token"] = device_token

    result = rh.login(email, password, **kwargs)
    if not result:
        print("ERROR: Robinhood login failed")
        sys.exit(1)
    print("[rh] Login successful")


def fetch_positions() -> list[dict]:
    raw = rh.account.get_all_positions()
    result = []
    for pos in raw:
        qty = float(pos.get("quantity", 0))
        if qty == 0:
            continue

        symbol = _symbol_from_instrument(pos.get("instrument", ""))
        avg_buy = float(pos.get("average_buy_price", 0))

        try:
            quote = rh.stocks.get_latest_price(symbol)
            current_price = float(quote[0]) if quote and quote[0] else avg_buy
        except Exception:
            current_price = avg_buy

        market_value = qty * current_price
        cost_basis = qty * avg_buy
        unrealized_pl = market_value - cost_basis
        unrealized_pl_pct = unrealized_pl / cost_basis if cost_basis > 0 else 0.0

        result.append({
            "symbol": symbol,
            "name": symbol,
            "type": "stock",
            "quantity": qty,
            "avg_buy_price": avg_buy,
            "current_price": round(current_price, 4),
            "equity": round(market_value, 2),
            "profit_loss": round(unrealized_pl, 2),
            "profit_loss_pct": round(unrealized_pl_pct * 100, 2),
            "percent_change": round(unrealized_pl_pct * 100, 2),
            "equity_change": round(unrealized_pl, 2),
        })
    return result


def fetch_open_orders() -> list[dict]:
    raw = rh.orders.get_all_open_stock_orders()
    result = []
    for o in raw:
        symbol = _symbol_from_instrument(o.get("instrument", ""))
        result.append({
            "order_id": o.get("id"),
            "symbol": symbol,
            "side": o.get("side", "").upper(),
            "order_type": o.get("type", "market"),
            "trigger": "stop" if o.get("type") in ("stop", "stop_limit") else "immediate",
            "state": o.get("state", "unknown"),
            "quantity": float(o.get("quantity", 0)),
            "limit_price": float(o["price"]) if o.get("price") else None,
            "stop_price": float(o["stop_price"]) if o.get("stop_price") else None,
            "created_at": o.get("created_at"),
            "updated_at": o.get("updated_at"),
        })
    return result


def fetch_account() -> dict:
    profile = rh.profiles.load_account_profile()
    portfolio = rh.profiles.load_portfolio_profile()
    return {
        "equity": float(portfolio.get("equity", 0)),
        "cash": float(profile.get("cash", 0)),
        "buying_power": float(profile.get("buying_power", 0)),
        "portfolio_value": float(portfolio.get("market_value", 0)),
    }


def upload_to_blob(token, site_id, blob_key, data):
    payload = json.dumps(data)
    url = f"{BLOBS_URL}/{site_id}/{STORE_NAME}/{blob_key}"
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    }
    print(f"  [blob] PUT {STORE_NAME}/{blob_key}")
    print(f"  [blob] Payload size: {len(payload)} bytes")
    resp = requests.put(url, headers=headers, data=payload, timeout=15)
    print(f"  [blob] Response: {resp.status_code} {resp.reason}")
    resp.raise_for_status()


def main():
    now = datetime.now(timezone.utc)
    print(f"[refresh] Order book refresh starting at {now.isoformat()}")

    token = os.getenv("NETLIFY_API_TOKEN")
    site_id = os.getenv("NETLIFY_SITE_ID")
    if not token or not site_id:
        print("ERROR: NETLIFY_API_TOKEN and NETLIFY_SITE_ID required")
        sys.exit(1)

    login()

    positions = fetch_positions()
    print(f"[refresh] Fetched {len(positions)} positions")

    open_orders = fetch_open_orders()
    print(f"[refresh] Fetched {len(open_orders)} open orders")

    account = fetch_account()
    print(f"[refresh] Account equity: ${account['equity']:,.2f}")

    # Build blob payload — keyed as "latest" so the frontend always reads the same key
    snapshot = {
        "timestamp": now.isoformat(),
        "account": account,
        "positions": positions,
        "open_orders": open_orders,
        "num_positions": len(positions),
        "num_open_orders": len(open_orders),
    }

    # Write to "latest" key so frontend can always fetch the current state
    upload_to_blob(token, site_id, "latest", snapshot)

    # Also write a timestamped copy for history
    ts_key = now.strftime("%Y-%m-%dT%H-%M-%S")
    upload_to_blob(token, site_id, ts_key, snapshot)

    print(f"[refresh] Done: {len(positions)} positions, {len(open_orders)} orders "
          f"-> {STORE_NAME}/latest + {STORE_NAME}/{ts_key}")

    rh.logout()


if __name__ == "__main__":
    main()
