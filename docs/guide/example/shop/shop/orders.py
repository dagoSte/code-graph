"""Order lifecycle: place, pay, cancel."""
from shop import inventory, notifications, payments
from shop.models import Order


def place_order(order_id, customer_id, lines):
    for line in lines:
        inventory.reserve(line.sku, line.quantity)
    return Order(order_id, customer_id, list(lines))


def pay_order(order):
    payments.capture(order, order.total())
    order.status = 'paid'


def cancel_order(order):
    """Cancel an order: give the stock back and refund what the customer paid."""
    if order.status == 'cancelled':
        return 0
    inventory.release_order(order)
    refunded = payments.refund(order, order.paid) if order.paid else 0
    order.status = 'cancelled'
    notifications.notify_cancelled(order, refunded)
    return refunded
