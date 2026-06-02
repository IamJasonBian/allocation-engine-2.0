"""IBKR account summary and positions via the Client Portal portfolio endpoints."""

import logging
from datetime import datetime, timezone

from app.brokers.ibkr.session import _result_data

log = logging.getLogger(__name__)


def _summary_field(summary: dict, *keys) -> float:
    """CP /portfolio/{acct}/summary values are nested as {key: {amount: x}}."""
    for k in keys:
        v = summary.get(k)
        if isinstance(v, dict):
            amt = v.get("amount")
            if amt is not None:
                return _to_float(amt)
        elif v is not None:
            return _to_float(v)
    return 0.0


def account(client, account_id: str) -> dict:
    """GET /portfolio/{account}/summary -> {equity, cash, buying_power, portfolio_value}."""
    summary = _result_data(client.get(f"portfolio/{account_id}/summary")) or {}
    if not isinstance(summary, dict):
        summary = {}
    equity = _summary_field(summary, "equitywithloanvalue", "netliquidation")
    return {
        "equity": equity,
        "cash": _summary_field(summary, "totalcashvalue", "availablefunds"),
        "buying_power": _summary_field(summary, "buyingpower"),
        "portfolio_value": _summary_field(summary, "netliquidation", "equitywithloanvalue"),
    }


def _raw_positions(client, account_id: str) -> list[dict]:
    data = _result_data(client.get(f"portfolio/{account_id}/positions/0"))
    if isinstance(data, list):
        return [p for p in data if isinstance(p, dict)]
    return []


def positions(client, account_id: str) -> list[dict]:
    """Return non-option positions in the standardized shape."""
    result = []
    for p in _raw_positions(client, account_id):
        if str(p.get("assetClass", "")).upper() == "OPT":
            continue
        qty = _to_float(p.get("position"))
        if not qty:
            continue
        avg = _to_float(p.get("avgCost") or p.get("avgPrice"))
        mkt_value = _to_float(p.get("mktValue"))
        cost_basis = qty * avg if avg else 0.0
        unrealized = (mkt_value - cost_basis) if mkt_value is not None else 0.0
        result.append({
            "symbol": p.get("contractDesc") or p.get("ticker") or "",
            "qty": qty,
            "side": "long" if qty > 0 else "short",
            "market_value": round(mkt_value, 2) if mkt_value is not None else 0.0,
            "avg_entry": avg or 0.0,
            "unrealized_pl": round(unrealized, 2),
            "unrealized_pl_pct": round(unrealized / cost_basis, 4) if cost_basis else 0.0,
        })
    return result


def options_positions(client, account_id: str) -> list[dict]:
    """Return option positions in the standardized options shape."""
    result = []
    for p in _raw_positions(client, account_id):
        if str(p.get("assetClass", "")).upper() != "OPT":
            continue
        qty = _to_float(p.get("position"))
        if not qty:
            continue

        chain_symbol = p.get("ticker") or _underlying_from_desc(p.get("contractDesc", "")) or ""
        multiplier = _to_float(p.get("multiplier")) or 100.0
        avg_cost = _to_float(p.get("avgCost"))  # per-contract cost (premium * mult)
        # avgCost is the total per-contract cost basis; per-share premium = avgCost / multiplier.
        avg_price = (avg_cost / multiplier) if avg_cost else 0.0
        mkt_price = _to_float(p.get("mktPrice")) or avg_price
        mkt_value = _to_float(p.get("mktValue"))

        strike = _to_float(p.get("strike")) or 0.0
        expiration = _format_expiry(p.get("expiry"))
        option_type = _right_to_type(p.get("putOrCall") or p.get("right"))
        dte = _dte(expiration)

        cost_basis = qty * avg_price * multiplier
        current_value = mkt_value if mkt_value is not None else qty * mkt_price * multiplier
        unrealized = current_value - cost_basis

        result.append({
            "chain_symbol": chain_symbol,
            "option_type": option_type,
            "position_type": "long" if qty > 0 else "short",
            "strike": strike,
            "expiration": expiration,
            "dte": dte,
            "quantity": qty,
            "avg_price": round(avg_price, 4),
            "mark_price": round(mkt_price, 4),
            "multiplier": multiplier,
            "cost_basis": round(cost_basis, 2),
            "current_value": round(current_value, 2),
            "unrealized_pl": round(unrealized, 2),
            "unrealized_pl_pct": round(unrealized / cost_basis, 4) if cost_basis else 0.0,
            "underlying_price": _to_float(p.get("undPrice")),
            "greeks": {"delta": None, "gamma": None, "theta": None, "vega": None, "iv": None},
        })
    return result


def _right_to_type(right) -> str:
    r = str(right or "").upper()
    if r in ("C", "CALL"):
        return "call"
    if r in ("P", "PUT"):
        return "put"
    return ""


def _format_expiry(expiry) -> str:
    """CP expiry is often 'YYYYMMDD'; normalise to 'YYYY-MM-DD'."""
    s = str(expiry or "")
    if len(s) == 8 and s.isdigit():
        return f"{s[0:4]}-{s[4:6]}-{s[6:8]}"
    return s


def _underlying_from_desc(desc: str) -> str:
    return str(desc).split(" ", 1)[0] if desc else ""


def _dte(expiration: str) -> int:
    if not expiration:
        return 0
    try:
        exp = datetime.strptime(expiration, "%Y-%m-%d").date()
        return (exp - datetime.now(timezone.utc).date()).days
    except (ValueError, TypeError):
        return 0


def _to_float(v):
    if v is None or v == "":
        return None
    try:
        return float(v)
    except (ValueError, TypeError):
        return None
