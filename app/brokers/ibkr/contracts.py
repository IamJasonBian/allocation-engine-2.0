"""Contract construction and qualification helpers for IBKR."""

import logging
from datetime import datetime

from ib_async import Option, Stock

log = logging.getLogger(__name__)


def to_ib_expiration(expiration: str) -> str:
    """Convert an ISO ``YYYY-MM-DD`` expiration to IB's ``YYYYMMDD`` format."""
    return datetime.strptime(expiration, "%Y-%m-%d").strftime("%Y%m%d")


def option_right(option_type: str) -> str:
    """Map ``'call'``/``'put'`` to IB's ``'C'``/``'P'`` right code."""
    ot = (option_type or "").strip().lower()
    if ot in ("call", "c"):
        return "C"
    if ot in ("put", "p"):
        return "P"
    raise ValueError(f"Unknown option_type: {option_type!r}")


def build_option(chain_symbol: str, expiration: str, strike: float,
                 option_type: str, exchange: str = "SMART") -> Option:
    """Build an (unqualified) :class:`ib_async.Option` contract.

    ``expiration`` is the ISO ``YYYY-MM-DD`` form; it is converted to IB's
    ``YYYYMMDD`` here.
    """
    return Option(
        chain_symbol,
        to_ib_expiration(expiration),
        float(strike),
        option_right(option_type),
        exchange,
    )


def build_stock(symbol: str, exchange: str = "SMART", currency: str = "USD") -> Stock:
    """Build an (unqualified) :class:`ib_async.Stock` contract."""
    return Stock(symbol, exchange, currency)
