"""Canonical data models shared across brokers, engine, and API."""

from dataclasses import dataclass, field


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
class FilledOrder:
    """A filled or cancelled order from history."""
    id: str
    symbol: str
    side: str
    qty: float
    order_type: str = "market"
    limit_price: float | None = None
    stop_price: float | None = None
    average_price: float | None = None
    filled_qty: float = 0.0
    status: str = "filled"
    created_at: str = ""
    updated_at: str = ""


@dataclass
class OptionPositionData:
    """An option position with Greeks and pricing."""
    chain_symbol: str
    option_type: str
    strike: float
    expiration: str
    quantity: float
    position_type: str
    avg_price: float
    mark_price: float
    multiplier: float = 100.0
    cost_basis: float = 0.0
    current_value: float = 0.0
    unrealized_pl: float = 0.0
    unrealized_pl_pct: float = 0.0
    underlying_price: float = 0.0
    break_even: float = 0.0
    delta: float = 0.0
    gamma: float = 0.0
    theta: float = 0.0
    vega: float = 0.0
    rho: float = 0.0
    iv: float = 0.0
    chance_of_profit: float = 0.0


@dataclass
class OptionOrder:
    """A filled/recent option order."""
    id: str
    state: str
    quantity: float
    price: float
    premium: float
    direction: str
    order_type: str = "limit"
    trigger: str = "immediate"
    time_in_force: str = "gfd"
    opening_strategy: str = ""
    created_at: str = ""
    updated_at: str = ""
    legs: list[dict] = field(default_factory=list)


@dataclass
class OrderResult:
    """Confirmation returned after submitting an order."""
    id: str
    symbol: str
    status: str | None = None
