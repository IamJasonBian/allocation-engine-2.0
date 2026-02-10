from .Order import Order
from .OrderType import OrderType


class Ticker:
    def __init__(self, orders: list[Order], target_open_orders: int = 0):
        self.orders = orders
        self.target_open_orders = target_open_orders

    def get_open_orders(self):
        return self.orders

    def get_valid_orders(self):
        return [order for order in self.orders if order.is_valid]

    def write_to_blob(self, blob):
        pass
