"""Core type schemas matching allocation-manager TypeScript contracts.

These Pydantic models define the shapes that allocation-manager's frontend
(robinhoodService.ts) expects from this engine's API responses. Any drift
between what the engine returns and what the frontend consumes will be
caught at deploy time via `scripts/check_schemas.py`.
"""

from dataclasses import asdict, dataclass
from typing import TypedDict

from pydantic import BaseModel, Field

from app.enums import (
    AssetType, OrderSide, OrderState, OrderTrigger, OrderType, QuoteState,
)


# -- Broker-level data shapes ------------------------------------------------
# These match the BrokerClient protocol return types and map to the
# allocation-manager TS interfaces: Position, Order, Portfolio


class PositionSchema(BaseModel):
    """Maps to allocation-manager Position / snapshot position format."""
    symbol: str
    qty: float
    side: str = Field(description="'long' or 'short'")
    market_value: float
    avg_entry: float
    unrealized_pl: float
    unrealized_pl_pct: float


class OrderSchema(BaseModel):
    """Maps to allocation-manager SnapshotOrder format."""
    id: str
    symbol: str
    side: str = Field(description="'BUY' or 'SELL'")
    qty: float
    type: str = Field(description="'market', 'limit', 'stop', 'stop_limit'")
    limit_price: float | None = None
    stop_price: float | None = None
    status: str


class AccountSchema(BaseModel):
    """Maps to allocation-manager Portfolio account fields."""
    equity: float
    cash: float
    buying_power: float
    portfolio_value: float


# -- API response envelopes --------------------------------------------------
# These match the JSON shapes returned by the Flask endpoints.


class AccountResponse(BaseModel):
    broker: str
    equity: float
    cash: float
    buying_power: float
    portfolio_value: float


class PositionsResponse(BaseModel):
    broker: str
    count: int
    positions: list[PositionSchema]


class OrdersResponse(BaseModel):
    broker: str
    count: int
    orders: list[OrderSchema]


class PortfolioResponse(BaseModel):
    broker: str
    equity: float | None
    cash: float | None
    buying_power: float | None
    portfolio_value: float | None
    positions: list[PositionSchema]


class HealthResponse(BaseModel):
    status: str
    service: str
    version: str
    enabled_brokers: list[str]
    default_broker: str
    engine_enabled: bool
    dry_run: bool
    timestamp: str


# -- Validation helpers -------------------------------------------------------


# -- Engine-internal shapes --------------------------------------------------
# This module imports nothing from the engine beyond app.enums, so the broker,
# loop, sweeps and planners can all import from it without a cycle.


class OrderEvent(TypedDict, total=False):
    """Normalised order record used by Redis/Blob sync and the API layer."""
    id: str
    symbol: str
    side: OrderSide
    order_type: OrderType
    asset_type: AssetType
    trigger: OrderTrigger
    state: OrderState | str
    quantity: float
    filled_quantity: float
    limit_price: float | None
    stop_price: float | None
    price: float | None                   # average fill price
    created_at: str
    updated_at: str
    # option-specific fields (absent for equities)
    legs: list[dict] | None
    direction: str | None                 # debit / credit
    opening_strategy: str | None
    premium: float | None
    processed_premium: float | None


@dataclass(frozen=True)
class OptionQuote:
    """One option quote at one instant; it decides its own usability.

    Lives inside the engine only. Rows that leave it (history blobs, Trading
    DB, API) carry ``to_row()``: ``json.dumps(default=str)`` would otherwise
    write this object out as its repr string.
    """
    state: QuoteState
    bid: float | None = None
    ask: float | None = None
    mark: float | None = None
    detail: str | None = None             # why, when not LIVE

    @property
    def exit_price(self) -> float | None:
        """What a seller can expect: the bid, or the mark when no bid is quoted."""
        return self.bid if self.bid is not None else self.mark

    @property
    def sellable(self) -> bool:
        """Live, with a price above zero to sell at."""
        return self.state is QuoteState.LIVE and (self.exit_price or 0) > 0

    def to_row(self) -> dict:
        """Plain-dict form for a position row."""
        return asdict(self)

    @classmethod
    def from_row(cls, position: dict) -> "OptionQuote":
        """Read the quote off a position row.

        Args:
            position: An ``OptionPosition``. Rows without a ``quote`` (other
                brokers, older snapshots) fall back to their ``mark_price``.

        Returns:
            The row's quote; MISSING when it has neither a quote nor a mark.
        """
        q = position.get("quote")
        if q:
            return cls(**{**q, "state": QuoteState(q["state"])})
        mark = position.get("mark_price")
        if mark is None:
            return cls(QuoteState.MISSING, detail="row has no quote")
        return cls(QuoteState.LIVE, mark=float(mark))


class OptionPosition(TypedDict, total=False):
    """One option position as ``BrokerClient.options_positions()`` returns it."""
    chain_symbol: str
    option_type: str                      # call / put (contract kind)
    position_type: str                    # long / short (direction)
    strike: float
    expiration: str                       # YYYY-MM-DD
    dte: int
    quantity: float
    avg_price: float                      # same as purchase_price; dashboard reads it
    purchase_price: float                 # premium per share
    mark_price: float                     # display value; purchase_price when the quote is MISSING
    quote: dict                           # OptionQuote.to_row(); decisions read this
    source: str
    multiplier: float
    cost_basis: float
    current_value: float
    unrealized_pl: float
    unrealized_pl_pct: float
    underlying_price: float | None
    greeks: dict


class OptionContract(TypedDict):
    """Identity of one option contract."""
    chain_symbol: str
    option_type: str
    strike: float
    expiration: str


class PlannedOptionStop(TypedDict):
    """A protective stop-limit the planner wants placed."""
    contract: OptionContract
    quantity: int
    entry: float
    stop_pct: float
    stop_price: float
    limit_price: float


class OptionStopSkip(TypedDict, total=False):
    """A long left without a stop: a stable reason code plus free-text detail."""
    contract: OptionContract
    reason: str
    detail: str | None


def validate_account(data: dict) -> AccountSchema:
    return AccountSchema.model_validate(data)


def validate_positions(data: list[dict]) -> list[PositionSchema]:
    return [PositionSchema.model_validate(p) for p in data]


def validate_orders(data: list[dict]) -> list[OrderSchema]:
    return [OrderSchema.model_validate(o) for o in data]
