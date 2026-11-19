"""Stock levels per SKU."""

STOCK = {}


def reserve(sku, quantity):
    if STOCK.get(sku, 0) < quantity:
        raise ValueError(f'not enough stock for {sku}')
    STOCK[sku] -= quantity


def release(sku, quantity):
    STOCK[sku] = STOCK.get(sku, 0) + quantity


def release_order(order):
    for line in order.lines:
        release(line.sku, line.quantity)
