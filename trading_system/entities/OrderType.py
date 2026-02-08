from enum import Enum

class OrderType(Enum):
    MARKET = "market"
    LIMIT = "limit"
    STOP = "stop"
    STOP_LIMIT = "stop_limit"
    TRAILING_STOP = "trailing_stop"

class TimeInForce(Enum):
    GFD = "good_for_day"
    GTC = "good_til_canceled"

class OrderSide(Enum):
    BUY = "buy"
    SELL = "sell"


