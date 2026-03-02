"""Canonical data models shared across brokers, engine, and API."""

from dataclasses import dataclass


@dataclass
class AccountSummary:
    equity: float
    cash: float
    buying_power: float
    portfolio_value: float


@dataclass
class Position:
    symbol: str
    qty: float
    side: str
    market_value: float
    avg_entry: float
    unrealized_pl: float
    unrealized_pl_pct: float


@dataclass
class Order:
    """An order to submit."""
    symbol: str
    side: str
    qty: float
    order_type: str = "market"
    limit_price: float | None = None
    stop_price: float | None = None


@dataclass
class OpenOrder:
    """A live order on the broker."""
    id: str
    symbol: str
    side: str
    qty: float
    order_type: str = "market"
    limit_price: float | None = None
    stop_price: float | None = None
    status: str = "unknown"


@dataclass
class OrderResult:
    """Confirmation returned after submitting an order."""
    id: str
    symbol: str
    status: str | None = None
