"""HTTP handlers (framework left out)."""
from shop.orders import cancel_order, pay_order, place_order

ORDERS = {}


def handle_place(request):
    order = place_order(request['id'], request['customer'], request['lines'])
    ORDERS[order.id] = order
    return {'status': order.status}


def handle_pay(request):
    pay_order(ORDERS[request['id']])
    return {'status': 'paid'}


def handle_cancel(request):
    refunded = cancel_order(ORDERS[request['id']])
    return {'status': 'cancelled', 'refunded': refunded}
