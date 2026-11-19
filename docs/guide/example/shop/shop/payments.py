"""Talks to the payment provider."""

LEDGER = []


def capture(order, amount):
    LEDGER.append(('capture', order.id, amount))
    order.paid += amount
    return amount


def refund(order, amount):
    if amount > order.paid:
        raise ValueError('refund larger than the captured amount')
    LEDGER.append(('refund', order.id, amount))
    order.paid -= amount
    return amount
