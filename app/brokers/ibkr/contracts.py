"""IBKR option contract resolution via the Client Portal secdef endpoints.

Resolution is a mandatory, ordered three-step dance:
  1. ``/iserver/secdef/search``  — resolve the underlying conid for the symbol
  2. ``/iserver/secdef/strikes`` — list valid months/strikes for OPT
  3. ``/iserver/secdef/info``    — resolve the exact option conid for a given
                                   strike / right / expiry (month)
"""

import logging
from datetime import datetime

from app.brokers.ibkr.session import _result_data

log = logging.getLogger(__name__)

# option_type -> CP "right" code
_RIGHT = {"call": "C", "put": "P"}


def _expiry_to_month(expiration: str) -> str:
    """Convert 'YYYY-MM-DD' to the CP month token 'MMMYY' (e.g. 2026-06-19 -> JUN26)."""
    dt = datetime.strptime(expiration, "%Y-%m-%d")
    return dt.strftime("%b%y").upper()


def resolve_option_conid(
    client,
    chain_symbol: str,
    expiration: str,
    strike: float,
    option_type: str,
) -> int | None:
    """Resolve an option contract to its IBKR conid.

    Returns the integer conid, or ``None`` if it cannot be resolved.
    """
    right = _RIGHT.get(option_type.lower())
    if right is None:
        log.error("[ibkr] Unknown option_type %r (expected call/put)", option_type)
        return None

    try:
        month = _expiry_to_month(expiration)
    except (ValueError, TypeError) as e:
        log.error("[ibkr] Bad expiration %r: %s", expiration, e)
        return None

    try:
        # 1. Resolve the underlying conid.
        search = _result_data(
            client.get(
                "iserver/secdef/search",
                params={"symbol": chain_symbol, "name": True, "secType": "STK"},
            )
        )
        underlying_conid = _extract_underlying_conid(search)
        if underlying_conid is None:
            log.error("[ibkr] Could not resolve underlying conid for %s", chain_symbol)
            return None

        # 2. Fetch valid strikes for the month (validates the chain exists).
        strikes = _result_data(
            client.get(
                "iserver/secdef/strikes",
                params={
                    "conid": underlying_conid,
                    "sectype": "OPT",
                    "month": month,
                },
            )
        )
        if not _strike_available(strikes, right, strike):
            log.error(
                "[ibkr] Strike %s %s not available for %s %s",
                strike, right, chain_symbol, month,
            )
            return None

        # 3. Resolve the exact option conid.
        info = _result_data(
            client.get(
                "iserver/secdef/info",
                params={
                    "conid": underlying_conid,
                    "sectype": "OPT",
                    "month": month,
                    "strike": strike,
                    "right": right,
                },
            )
        )
        conid = _extract_option_conid(info)
        if conid is None:
            log.error(
                "[ibkr] secdef/info returned no conid for %s %s %s %s",
                chain_symbol, month, strike, right,
            )
        return conid
    except Exception:
        log.exception("[ibkr] Option conid resolution failed for %s %s %s %s",
                      chain_symbol, expiration, strike, option_type)
        return None


def _extract_underlying_conid(search) -> int | None:
    """secdef/search returns a list of matches; take the first conid."""
    if isinstance(search, list) and search:
        first = search[0]
        if isinstance(first, dict):
            conid = first.get("conid")
            if conid is not None:
                return int(conid)
    return None


def _strike_available(strikes, right, strike: float) -> bool:
    """secdef/strikes returns {'call': [...], 'put': [...]} of available strikes."""
    if not isinstance(strikes, dict):
        return False
    key = "call" if right == "C" else "put"
    available = strikes.get(key) or []
    # Compare with float tolerance.
    return any(abs(float(s) - float(strike)) < 1e-6 for s in available)


def _extract_option_conid(info) -> int | None:
    """secdef/info returns a list of contract dicts; take the first conid."""
    if isinstance(info, list) and info:
        first = info[0]
        if isinstance(first, dict):
            conid = first.get("conid")
            if conid is not None:
                return int(conid)
    elif isinstance(info, dict):
        conid = info.get("conid")
        if conid is not None:
            return int(conid)
    return None
