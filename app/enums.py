"""Shared enums for order side, type, asset type, state, trigger, and risk events."""

from enum import StrEnum


class OrderSide(StrEnum):
    BUY = "BUY"
    SELL = "SELL"


class OrderType(StrEnum):
    MARKET = "market"
    LIMIT = "limit"
    STOP = "stop"
    STOP_LIMIT = "stop_limit"


class AssetType(StrEnum):
    EQUITY = "equity"
    OPTION = "option"
    SHADOW_EQUITY = "shadow_equity"


class OrderState(StrEnum):
    QUEUED = "queued"
    UNCONFIRMED = "unconfirmed"
    CONFIRMED = "confirmed"
    PARTIALLY_FILLED = "partially_filled"
    PENDING = "pending"
    FILLED = "filled"
    CANCELLED = "cancelled"
    UNKNOWN = "unknown"


OPEN_STATES = frozenset({
    OrderState.QUEUED,
    OrderState.UNCONFIRMED,
    OrderState.CONFIRMED,
    OrderState.PARTIALLY_FILLED,
    OrderState.PENDING,
})


class OrderTrigger(StrEnum):
    IMMEDIATE = "immediate"
    STOP = "stop"


class RiskEventType(StrEnum):
    """Risk events emitted during engine reconciliation."""
    PRICE_DEPEG = "price_depeg"             # live price diverges from cached/stale price
    POSITION_LIMIT = "position_limit"       # position exceeds concentration limit
    ORDER_REJECTED = "order_rejected"       # broker rejected an order
