"""Back-office tools."""
from shop.orders import cancel_order


def cancel_unpaid(orders):
    return [cancel_order(order) for order in orders if order.status == 'open']
