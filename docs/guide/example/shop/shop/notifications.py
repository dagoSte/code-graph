"""Messages to customers."""

OUTBOX = []


def send_email(customer_id, subject, body):
    OUTBOX.append((customer_id, subject, body))


def notify_cancelled(order, refunded):
    send_email(order.customer_id, f'Order {order.id} cancelled',
               f'We refunded {refunded / 100:.2f}.')
