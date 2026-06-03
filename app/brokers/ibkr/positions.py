"""Pure mapping functions: IBKR objects -> standardized engine dict shapes.

These are deliberately free of any network/event-loop concerns so they can be
unit-tested with plain MagicMock objects.
"""

import logging
from datetime import datetime, timezone

log = logging.getLogger(__name__)


def _f(value, default: float = 0.0) -> float:
    """Best-effort float coercion."""
    try:
        if value is None:
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


# -- account ----------------------------------------------------------------

def map_account_summary(rows) -> dict:
    """Map ``ib.accountSummary()`` rows to ``{equity, cash, buying_power,
    portfolio_value}``.

    Each row is an ``AccountValue``-like object with ``.tag`` and ``.value``.
    """
    by_tag: dict[str, str] = {}
    for row in (rows or []):
        tag = getattr(row, "tag", None)
        if tag is not None:
            by_tag[tag] = getattr(row, "value", None)

    return {
        "equity": _f(by_tag.get("NetLiquidation")),
        "cash": _f(by_tag.get("TotalCashValue")),
        "buying_power": _f(by_tag.get("BuyingPower")),
        "portfolio_value": _f(by_tag.get("GrossPositionValue")),
    }


# -- equity positions --------------------------------------------------------

def map_position(item) -> dict | None:
    """Map an ``ib.portfolio()`` item (PortfolioItem) to the standardized
    equity position dict, or ``None`` if it isn't a stock / is flat."""
    contract = getattr(item, "contract", None)
    if contract is None:
        return None
    sec_type = getattr(contract, "secType", "")
    if sec_type != "STK":
        return None

    qty = _f(getattr(item, "position", 0))
    if qty == 0:
        return None

    avg_entry = _f(getattr(item, "averageCost", 0))
    market_value = _f(getattr(item, "marketValue", 0))
    unrealized_pl = _f(getattr(item, "unrealizedPNL", 0))
    cost_basis = avg_entry * qty
    unrealized_pl_pct = (unrealized_pl / cost_basis) if cost_basis else 0.0

    return {
        "symbol": getattr(contract, "symbol", ""),
        "qty": qty,
        "side": "long" if qty > 0 else "short",
        "market_value": round(market_value, 2),
        "avg_entry": round(avg_entry, 4),
        "unrealized_pl": round(unrealized_pl, 2),
        "unrealized_pl_pct": round(unrealized_pl_pct, 4),
    }


# -- open orders -------------------------------------------------------------

def map_open_trade(trade) -> dict:
    """Map an ``ib.openTrades()`` Trade to the standardized open-order dict."""
    order = getattr(trade, "order", None)
    contract = getattr(trade, "contract", None)
    status = getattr(trade, "orderStatus", None)

    lmt = _f(getattr(order, "lmtPrice", 0)) if order else 0.0
    aux = _f(getattr(order, "auxPrice", 0)) if order else 0.0

    return {
        "id": str(getattr(order, "orderId", "")) if order else "",
        "symbol": getattr(contract, "symbol", "") if contract else "",
        "side": (getattr(order, "action", "") if order else "").upper(),
        "qty": _f(getattr(order, "totalQuantity", 0)) if order else 0.0,
        "type": getattr(order, "orderType", "") if order else "",
        "limit_price": lmt if lmt else None,
        "stop_price": aux if aux else None,
        "status": getattr(status, "status", "") if status else "",
    }


# -- options positions -------------------------------------------------------

def _dte(expiration_iso: str) -> int:
    if not expiration_iso:
        return 0
    try:
        exp = datetime.strptime(expiration_iso, "%Y-%m-%d").date()
        return (exp - datetime.now(timezone.utc).date()).days
    except (ValueError, TypeError):
        return 0


def _ib_exp_to_iso(ib_exp: str) -> str:
    """Convert IB ``YYYYMMDD`` to ISO ``YYYY-MM-DD`` (best-effort)."""
    if not ib_exp:
        return ""
    try:
        return datetime.strptime(ib_exp, "%Y%m%d").strftime("%Y-%m-%d")
    except (ValueError, TypeError):
        return ib_exp


def map_option_position(item, ticker=None) -> dict | None:
    """Map an ``ib.portfolio()`` OPT item (+ optional live ticker for greeks)
    to the standardized options-position dict, or ``None`` if not an option /
    is flat."""
    contract = getattr(item, "contract", None)
    if contract is None:
        return None
    if getattr(contract, "secType", "") != "OPT":
        return None

    qty = _f(getattr(item, "position", 0))
    if qty == 0:
        return None

    right = (getattr(contract, "right", "") or "").upper()
    option_type = "call" if right.startswith("C") else "put" if right.startswith("P") else ""
    expiration = _ib_exp_to_iso(getattr(contract, "lastTradeDateOrContractMonth", ""))
    strike = _f(getattr(contract, "strike", 0))
    multiplier = _f(getattr(contract, "multiplier", 100)) or 100.0

    avg_cost = _f(getattr(item, "averageCost", 0))
    # IB averageCost for options is per-contract (already times multiplier);
    # normalize to per-share avg price for the standardized shape.
    avg_price = (avg_cost / multiplier) if multiplier else avg_cost

    greeks = {"delta": None, "gamma": None, "theta": None, "vega": None, "iv": None}
    mark_price = avg_price
    underlying_price = None
    if ticker is not None:
        mg = getattr(ticker, "modelGreeks", None)
        if mg is not None:
            for g in ("delta", "gamma", "theta", "vega"):
                val = getattr(mg, g, None)
                if val is not None:
                    greeks[g] = round(_f(val), 6)
            iv = getattr(mg, "impliedVol", None)
            if iv is not None:
                greeks["iv"] = round(_f(iv), 6)
            undp = getattr(mg, "undPrice", None)
            if undp is not None:
                underlying_price = round(_f(undp), 4)
        mp = getattr(ticker, "marketPrice", None)
        try:
            mp_val = mp() if callable(mp) else mp
            if mp_val is not None and _f(mp_val) > 0:
                mark_price = _f(mp_val)
        except Exception:
            pass

    cost_basis = qty * avg_price * multiplier
    current_value = qty * mark_price * multiplier
    unrealized_pl = current_value - cost_basis

    return {
        "chain_symbol": getattr(contract, "symbol", ""),
        "option_type": option_type,
        "position_type": "long" if qty > 0 else "short",
        "strike": strike,
        "expiration": expiration,
        "dte": _dte(expiration),
        "quantity": qty,
        "avg_price": round(avg_price, 4),
        "mark_price": round(mark_price, 4),
        "multiplier": multiplier,
        "cost_basis": round(cost_basis, 2),
        "current_value": round(current_value, 2),
        "unrealized_pl": round(unrealized_pl, 2),
        "unrealized_pl_pct": round(unrealized_pl / cost_basis, 4) if cost_basis else 0.0,
        "underlying_price": underlying_price,
        "greeks": greeks,
    }


# -- options orders ----------------------------------------------------------

def map_option_trade(trade) -> dict:
    """Map an option ``Trade`` to the standardized options-order dict."""
    order = getattr(trade, "order", None)
    contract = getattr(trade, "contract", None)
    status = getattr(trade, "orderStatus", None)

    qty = _f(getattr(order, "totalQuantity", 0)) if order else 0.0
    action = (getattr(order, "action", "") if order else "").upper()
    direction = "debit" if action == "BUY" else "credit" if action == "SELL" else ""
    lmt = _f(getattr(order, "lmtPrice", 0)) if order else 0.0
    state = getattr(status, "status", "") if status else ""

    right = (getattr(contract, "right", "") or "").upper() if contract else ""
    option_type = "call" if right.startswith("C") else "put" if right.startswith("P") else ""
    expiration = _ib_exp_to_iso(
        getattr(contract, "lastTradeDateOrContractMonth", "") if contract else ""
    )
    chain_symbol = getattr(contract, "symbol", "") if contract else ""
    strike = _f(getattr(contract, "strike", 0)) if contract else 0.0

    position_effect = "open"
    if order is not None:
        oca = getattr(order, "openClose", "") or ""
        if oca.upper().startswith("C"):
            position_effect = "close"

    legs = [{
        "side": action,
        "position_effect": position_effect,
        "quantity": qty,
        "strike": strike,
        "expiration": expiration,
        "option_type": option_type,
        "chain_symbol": chain_symbol,
    }] if contract is not None else []

    return {
        "order_id": str(getattr(order, "orderId", "")) if order else "",
        "state": state,
        "quantity": qty,
        "price": lmt if lmt else 0.0,
        "premium": round(lmt * qty * 100, 2) if lmt else 0.0,
        "processed_premium": 0.0,
        "direction": direction,
        "order_type": getattr(order, "orderType", "") if order else "",
        "trigger": "immediate",
        "time_in_force": getattr(order, "tif", "") if order else "",
        "opening_strategy": "",
        "created_at": "",
        "updated_at": "",
        "legs": legs,
    }
