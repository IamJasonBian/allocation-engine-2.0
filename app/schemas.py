"""Core type schemas matching allocation-manager TypeScript contracts.

These Pydantic models define the shapes that allocation-manager's frontend
(robinhoodService.ts) expects from this engine's API responses. Any drift
between what the engine returns and what the frontend consumes will be
caught at deploy time via `scripts/check_schemas.py`.
"""

from pydantic import BaseModel, Field


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


def validate_account(data: dict) -> AccountSchema:
    return AccountSchema.model_validate(data)


def validate_positions(data: list[dict]) -> list[PositionSchema]:
    return [PositionSchema.model_validate(p) for p in data]


def validate_orders(data: list[dict]) -> list[OrderSchema]:
    return [OrderSchema.model_validate(o) for o in data]
