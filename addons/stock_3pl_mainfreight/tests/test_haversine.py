# addons/stock_3pl_mainfreight/tests/test_haversine.py
"""Pure-Python tests for the haversine distance utility.

These tests use unittest.TestCase (not TransactionCase) because haversine_km
has no Odoo ORM dependencies. Run with:
    python -m pytest addons/stock_3pl_mainfreight/tests/test_haversine.py -v

These tests will NOT run under odoo-bin --test-enable --test-tags=routing
because they lack the @tagged decorator. Add the tests to your pytest run
instead of the Odoo test runner for this module.
"""
import unittest
from odoo.addons.stock_3pl_mainfreight.utils.haversine import haversine_km, sort_warehouses_by_distance


class TestHaversine(unittest.TestCase):

    def test_same_point_is_zero(self):
        self.assertAlmostEqual(
            haversine_km(-37.787, 175.279, -37.787, 175.279), 0.0, places=2
        )

    def test_hamilton_to_christchurch_approx_676km(self):
        # Hamilton NZ (-37.7870, 175.2793) → Christchurch NZ (-43.5321, 172.6362)
        # Great-circle distance is ~676 km (road distance is longer ~750 km)
        km = haversine_km(-37.7870, 175.2793, -43.5321, 172.6362)
        self.assertGreater(km, 670)
        self.assertLess(km, 685)

    def test_sort_returns_closest_first(self):
        # Customer near Hamilton
        customer_lat, customer_lng = -37.0, 175.0
        warehouses = [
            {'id': 'chc', 'lat': -43.5321, 'lng': 172.6362},  # Christchurch ~760km
            {'id': 'ham', 'lat': -37.7870, 'lng': 175.2793},  # Hamilton ~100km
        ]
        sorted_wh = sort_warehouses_by_distance(customer_lat, customer_lng, warehouses)
        self.assertEqual(sorted_wh[0]['id'], 'ham')

    def test_sort_empty_list_returns_empty(self):
        result = sort_warehouses_by_distance(-37.0, 175.0, [])
        self.assertEqual(result, [])

    def test_sort_does_not_modify_original(self):
        warehouses = [
            {'id': 'far', 'lat': -43.5321, 'lng': 172.6362},
            {'id': 'near', 'lat': -37.7870, 'lng': 175.2793},
        ]
        original_first = warehouses[0]['id']
        sort_warehouses_by_distance(-37.0, 175.0, warehouses)
        self.assertEqual(warehouses[0]['id'], original_first)

    def test_northern_hemisphere(self):
        # London to Paris ~340km
        km = haversine_km(51.5074, -0.1278, 48.8566, 2.3522)
        self.assertGreater(km, 300)
        self.assertLess(km, 400)

    def test_invalid_latitude_raises_value_error(self):
        with self.assertRaises(ValueError):
            haversine_km(91, 0, 0, 0)

    def test_invalid_longitude_raises_value_error(self):
        with self.assertRaises(ValueError):
            haversine_km(0, 181, 0, 0)
