"""Shared enums for order side, type, asset type, state, and trigger."""

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
