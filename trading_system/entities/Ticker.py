from .Order import Order
from .OrderType import OrderType

class Ticker:
    def __init__(self, orders: list[Order]):
        self.orders = orders

    def get_open_orders(self):
        return self.orders

    def get_valid_orders(self):
        return [order for order in self.orders if order.is_valid]

    '''
    
        #Passthroughs - Not needed here 
        @staticmethod
        def mark_order(self, order: Order):
            order.mark_valid()
                
    '''

    def write_to_blob(self, blob):
        pass



