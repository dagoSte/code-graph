import unittest

from shop import api, inventory
from shop.models import Line


class Api(unittest.TestCase):
    def test_cancel_endpoint(self):
        inventory.STOCK['pen'] = 3
        api.handle_place({'id': 'a1', 'customer': 'c2', 'lines': [Line('pen', 1, 200)]})
        api.handle_pay({'id': 'a1'})
        self.assertEqual(api.handle_cancel({'id': 'a1'})['refunded'], 200)
