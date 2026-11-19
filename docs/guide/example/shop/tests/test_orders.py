import unittest

from shop import inventory
from shop.models import Line
from shop.orders import cancel_order, pay_order, place_order


class Orders(unittest.TestCase):
    def setUp(self):
        inventory.STOCK.clear()
        inventory.STOCK['book'] = 5

    def test_cancel_refunds_and_restocks(self):
        order = place_order('o1', 'c1', [Line('book', 2, 1500)])
        pay_order(order)
        self.assertEqual(cancel_order(order), 3000)
        self.assertEqual(inventory.STOCK['book'], 5)

    def test_cancel_twice_refunds_once(self):
        order = place_order('o2', 'c1', [Line('book', 1, 1500)])
        pay_order(order)
        cancel_order(order)
        self.assertEqual(cancel_order(order), 0)
