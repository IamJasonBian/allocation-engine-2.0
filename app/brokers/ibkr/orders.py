"""Order builders for IBKR — always Pegged-to-Stock for options.

These are pure constructors (no network / event loop) so they unit-test with
plain assertions. The orchestration that qualifies contracts, fetches greeks
and places the order lives in :mod:`app.brokers.ibkr.client`.
"""

import logging

from ib_async import Order
from ib_async.order import PriceCondition

log = logging.getLogger(__name__)

PEG_STK = "PEG STK"
# Order states that mean the PEG STK order did not take — trigger the LMT fallback.
_REJECTED_STATES = {"rejected", "inactive"}


def signed_delta(option_type: str, delta: float) -> float:
    """PEG STK delta convention: positive for calls, negative for puts.

    ``delta`` is supplied as a magnitude; its sign is derived from the right.
    """
    mag = abs(float(delta))
    return mag if (option_type or "").strip().lower().startswith("c") else -mag


def build_price_conditions(underlying_conid: int, above=None, below=None):
    """Build PriceCondition(s) on the underlying for a cancel-on-cross order.

    Returns ``(conditions, conditions_cancel_order)``. When both bounds are
    given the conditions are OR-joined so the order cancels when the underlying
    leaves the ``[below, above]`` band.
    """
    conditions = []
    if above is not None:
        conditions.append(
            PriceCondition(conId=int(underlying_conid), exch="SMART",
                           isMore=True, price=float(above))
        )
    if below is not None:
        conditions.append(
            PriceCondition(conId=int(underlying_conid), exch="SMART",
                           isMore=False, price=float(below))
        )
    if len(conditions) > 1:
        for c in conditions:
            c.conjunction = "o"  # OR — cancel if outside the band
    return conditions, bool(conditions)


def build_peg_stk_order(action: str, quantity: float, delta: float,
                        starting_price=None, conditions=None,
                        conditions_cancel: bool = False) -> Order:
    """Build a Pegged-to-Stock (``PEG STK``) option order (DAY)."""
    o = Order()
    o.orderType = PEG_STK
    o.action = (action or "").upper()
    o.totalQuantity = float(quantity)
    o.delta = float(delta)
    if starting_price is not None:
        o.startingPrice = float(starting_price)
    o.tif = "DAY"
    if conditions:
        o.conditions = list(conditions)
        o.conditionsCancelOrder = bool(conditions_cancel)
    return o


def build_limit_fallback(action: str, quantity: float, limit_price: float,
                         conditions=None, conditions_cancel: bool = False) -> Order:
    """Fallback LMT order at the peg starting price when an exchange rejects PEG STK."""
    o = Order()
    o.orderType = "LMT"
    o.action = (action or "").upper()
    o.totalQuantity = float(quantity)
    o.lmtPrice = float(limit_price or 0.0)
    o.tif = "DAY"
    if conditions:
        o.conditions = list(conditions)
        o.conditionsCancelOrder = bool(conditions_cancel)
    return o


def build_equity_order(action: str, quantity: float, order_type: str = "MKT",
                       limit_price=None) -> Order:
    """Build a simple equity MKT/LMT order."""
    o = Order()
    o.action = (action or "").upper()
    o.totalQuantity = float(quantity)
    o.tif = "DAY"
    if (order_type or "").upper() == "LMT" and limit_price is not None:
        o.orderType = "LMT"
        o.lmtPrice = float(limit_price)
    else:
        o.orderType = "MKT"
    return o


def order_is_rejected(trade) -> bool:
    """True if a placed Trade came back rejected/inactive (PEG STK unsupported)."""
    status = getattr(trade, "orderStatus", None)
    s = (getattr(status, "status", "") or "").strip().lower() if status else ""
    return s in _REJECTED_STATES
