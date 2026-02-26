from trading_system.entities.OrderType import OrderSide


class Order:
    def __init__(self, size, price, order_type, side=None, order_id=None, created_at=None):
        self.size = size
        self.price = price
        self.order_type = order_type
        self.side = side
        self.order_id = order_id
        self.created_at = created_at
        self.is_valid = True

        # Execution quality tracking
        self.submitted_bid = None       # bid when order was placed
        self.submitted_ask = None       # ask when order was placed
        self.submitted_mid = None       # (bid + ask) / 2 when placed
        self.submitted_spread = None    # ask - bid when placed
        self.fill_price = None          # actual fill price from broker
        self.fill_timestamp = None      # when fill occurred
        self.slippage_bps = None        # slippage vs mid in basis points
        self.submission_id = None       # links to FillLogger record

    def mark_invalid(self) -> bool:
        self.is_valid = False

    def get_state(self) -> dict:
        return {
            "size": self.size,
            "price": self.price,
            "order_type": self.order_type,
            "is_valid": self.is_valid,
            "side": self.side,
            "order_id": self.order_id,
            "created_at": self.created_at,
            "submitted_bid": self.submitted_bid,
            "submitted_ask": self.submitted_ask,
            "submitted_mid": self.submitted_mid,
            "submitted_spread": self.submitted_spread,
            "fill_price": self.fill_price,
            "fill_timestamp": self.fill_timestamp,
            "slippage_bps": self.slippage_bps,
            "submission_id": self.submission_id,
        }

