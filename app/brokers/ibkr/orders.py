"""IBKR order placement / cancellation via the Client Portal Web API.

Order placement on the CP API is two-phase: the initial POST may return an
array of "reply" objects (warnings / confirmations) each with an ``id`` and a
``message``. Each must be confirmed via ``POST /iserver/reply/{id}`` until a
real order id (or a rejection) is returned.
"""

import logging

from app.brokers.ibkr.session import _result_data
from app.enums import OrderType

log = logging.getLogger(__name__)

# Max reply/confirm round-trips before giving up (guards against a loop).
_MAX_REPLY_HOPS = 10

# CP order states considered "open" / live.
_OPEN_ORDER_STATES = {
    "PreSubmitted", "Submitted", "PendingSubmit", "PendingCancel", "Inactive",
}


def _cp_order_type(order_type: str | None, limit_price) -> str:
    """Map an app order type to a CP orderType ('LMT' / 'MKT')."""
    if order_type is not None and str(order_type).lower() == OrderType.LIMIT and limit_price is not None:
        return "LMT"
    if limit_price is not None and order_type is None:
        return "LMT"
    return "MKT"


def _normalize_side(side: str) -> str:
    return str(side).upper()


def _build_order_body(conid: int, side: str, order_type: str, quantity, price=None) -> dict:
    body = {
        "conid": int(conid),
        "side": _normalize_side(side),
        "orderType": order_type,
        "quantity": int(quantity),
        "tif": "DAY",
    }
    if order_type == "LMT" and price is not None:
        body["price"] = float(price)
    return body


def _place_with_confirm(client, account_id: str, order_body: dict):
    """POST the order then walk the reply/confirm chain.

    Returns the final order payload dict (containing an 'order_id' / 'id') or
    None if the order was rejected or no id could be obtained.
    """
    result = client.post(
        f"iserver/account/{account_id}/orders",
        params={"orders": [order_body]},
    )
    data = _result_data(result)

    hops = 0
    while hops < _MAX_REPLY_HOPS:
        order_dict = _extract_order_dict(data)
        if order_dict is not None:
            return order_dict

        reply_id = _extract_reply_id(data)
        if reply_id is None:
            # Neither a confirmable reply nor a resolvable order id.
            log.error("[ibkr] Unrecognised order response: %s", data)
            return None

        # Check for an explicit rejection in the reply message.
        if _is_rejection(data):
            log.error("[ibkr] Order rejected: %s", data)
            return None

        reply = client.post(
            f"iserver/reply/{reply_id}",
            params={"confirmed": True},
        )
        data = _result_data(reply)
        hops += 1

    log.error("[ibkr] Exceeded reply/confirm hops (%d) without a final order id", _MAX_REPLY_HOPS)
    return None


def _extract_order_dict(data) -> dict | None:
    """Return the dict carrying a real order id, if present."""
    items = data if isinstance(data, list) else [data]
    for item in items:
        if isinstance(item, dict) and (item.get("order_id") or item.get("orderId")):
            return item
    return None


def _extract_reply_id(data) -> str | None:
    """Return the first reply/confirmation id requiring acknowledgement."""
    items = data if isinstance(data, list) else [data]
    for item in items:
        if not isinstance(item, dict):
            continue
        rid = item.get("id")
        # A reply object has an 'id' + a 'message' (list of warnings).
        if rid is not None and "message" in item:
            return str(rid)
    return None


def _is_rejection(data) -> bool:
    items = data if isinstance(data, list) else [data]
    for item in items:
        if isinstance(item, dict):
            if item.get("error"):
                return True
            msgs = item.get("message")
            if isinstance(msgs, list):
                for m in msgs:
                    if "reject" in str(m).lower():
                        return True
    return False


def _order_id_of(order_dict: dict) -> str:
    return str(order_dict.get("order_id") or order_dict.get("orderId") or "")


def _order_status_of(order_dict: dict) -> str:
    return str(order_dict.get("order_status") or order_dict.get("status") or "")


# -- public order operations -------------------------------------------------

def submit_order(client, account_id: str, order: dict) -> dict | None:
    """Submit an equity order. Returns {id, symbol, status} or None on failure."""
    symbol = order.get("symbol", "")
    side = order.get("side", "")
    qty = order.get("quantity")
    order_type = order.get("order_type")
    limit_px = order.get("limit_price")
    conid = order.get("conid")

    try:
        if conid is None:
            log.error("[ibkr] submit_order requires a 'conid' for %s", symbol)
            return None
        cp_type = _cp_order_type(order_type, limit_px)
        body = _build_order_body(conid, side, cp_type, qty, limit_px)
        order_dict = _place_with_confirm(client, account_id, body)
        if order_dict is None:
            return None
        oid = _order_id_of(order_dict)
        log.info("[ibkr] Order submitted: %s %s conid=%s -> %s",
                 _normalize_side(side), qty, conid, oid)
        return {"id": oid, "symbol": symbol, "status": _order_status_of(order_dict)}
    except Exception as e:
        log.error("[ibkr] submit_order error for %s: %s", symbol, e)
        return None


def submit_option_order(client, account_id: str, conid: int, order: dict) -> dict | None:
    """Submit an option order against an already-resolved conid.

    Returns {id, symbol, status} or None on failure.
    """
    chain_symbol = order.get("chain_symbol", "")
    side = order.get("side", "")
    qty = order.get("quantity")
    order_type = order.get("order_type")
    limit_px = order.get("limit_price")

    try:
        cp_type = _cp_order_type(order_type, limit_px)
        body = _build_order_body(conid, side, cp_type, qty, limit_px)
        order_dict = _place_with_confirm(client, account_id, body)
        if order_dict is None:
            return None
        oid = _order_id_of(order_dict)
        log.info("[ibkr] Option order submitted: %s %s conid=%s -> %s",
                 _normalize_side(side), qty, conid, oid)
        return {"id": oid, "symbol": chain_symbol, "status": _order_status_of(order_dict)}
    except Exception as e:
        log.error("[ibkr] submit_option_order error for %s: %s", chain_symbol, e)
        return None


def cancel_order(client, account_id: str, order_id: str) -> None:
    client.delete(f"iserver/account/{account_id}/order/{order_id}")
    log.info("[ibkr] Cancelled order %s", order_id)


def cancel_all(client, account_id: str) -> None:
    for o in open_orders(client, account_id):
        oid = o.get("id")
        if oid:
            try:
                cancel_order(client, account_id, oid)
            except Exception as e:
                log.warning("[ibkr] Failed to cancel order %s: %s", oid, e)
    log.info("[ibkr] cancel_all complete")


def _raw_live_orders(client) -> list[dict]:
    data = _result_data(client.get("iserver/account/orders"))
    if isinstance(data, dict):
        orders = data.get("orders") or []
    elif isinstance(data, list):
        orders = data
    else:
        orders = []
    return [o for o in orders if isinstance(o, dict)]


def open_orders(client, account_id: str) -> list[dict]:
    """Return open equity orders in the standardized shape."""
    result = []
    for o in _raw_live_orders(client):
        status = o.get("status") or o.get("order_status") or ""
        if status not in _OPEN_ORDER_STATES:
            continue
        # Skip option legs; this is the equity-shaped view.
        if str(o.get("secType", "")).upper() == "OPT":
            continue
        result.append({
            "id": str(o.get("orderId") or o.get("order_id") or ""),
            "symbol": o.get("ticker") or o.get("symbol") or "",
            "side": str(o.get("side", "")).upper(),
            "qty": _to_float(o.get("totalSize") if o.get("totalSize") is not None else o.get("remainingQuantity")),
            "type": str(o.get("orderType", "")).lower(),
            "limit_price": _to_float(o.get("price")),
            "stop_price": _to_float(o.get("stop_price") or o.get("auxPrice")),
            "status": status,
        })
    return result


def options_orders(client, limit: int = 50, open_only: bool = False) -> list[dict]:
    """Return recent option orders in the standardized options shape."""
    result = []
    for o in _raw_live_orders(client):
        if str(o.get("secType", "")).upper() != "OPT":
            continue
        status = o.get("status") or o.get("order_status") or ""
        if open_only and status not in _OPEN_ORDER_STATES:
            continue
        side = str(o.get("side", "")).upper()
        leg = {
            "side": side,
            "position_effect": "open",
            "quantity": _to_float(o.get("totalSize")),
            "strike": _to_float(o.get("strike")),
            "expiration": o.get("expiry") or o.get("expiration") or "",
            "option_type": _right_to_type(o.get("right")),
            "chain_symbol": o.get("ticker") or o.get("symbol") or "",
        }
        result.append({
            "order_id": str(o.get("orderId") or o.get("order_id") or ""),
            "state": status,
            "quantity": _to_float(o.get("totalSize")),
            "price": _to_float(o.get("price")),
            "premium": _to_float(o.get("price")),
            "processed_premium": _to_float(o.get("avgPrice")),
            "direction": "credit" if side == "SELL" else "debit",
            "order_type": str(o.get("orderType", "")).lower(),
            "trigger": "immediate",
            "time_in_force": o.get("timeInForce") or o.get("tif") or "",
            "opening_strategy": "",
            "created_at": o.get("lastExecutionTime") or "",
            "updated_at": o.get("lastExecutionTime") or "",
            "legs": [leg],
        })
        if len(result) >= limit:
            break
    return result


def _right_to_type(right) -> str:
    r = str(right or "").upper()
    if r == "C":
        return "call"
    if r == "P":
        return "put"
    return ""


def _to_float(v):
    if v is None or v == "":
        return None
    try:
        return float(v)
    except (ValueError, TypeError):
        return None
