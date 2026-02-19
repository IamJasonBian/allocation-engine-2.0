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
        }

