# addons/stock_3pl_mainfreight/utils/haversine.py
from math import radians, cos, sin, asin, sqrt


def haversine_km(lat1, lon1, lat2, lon2):
    """Return the great-circle distance in kilometres between two points.

    Uses the haversine formula. Accurate to ~0.5% for distances < 10,000 km.
    All inputs in decimal degrees.
    """
    lat1, lon1, lat2, lon2 = map(radians, [lat1, lon1, lat2, lon2])
    dlat = lat2 - lat1
    dlon = lon2 - lon1
    a = sin(dlat / 2) ** 2 + cos(lat1) * cos(lat2) * sin(dlon / 2) ** 2
    return 6371 * 2 * asin(sqrt(a))


def sort_warehouses_by_distance(customer_lat, customer_lng, warehouses):
    """Sort a list of warehouse dicts by distance to the customer (closest first).

    Each dict must have 'lat' and 'lng' keys. Returns a NEW sorted list;
    does not modify the original.
    """
    return sorted(
        warehouses,
        key=lambda wh: haversine_km(customer_lat, customer_lng, wh['lat'], wh['lng']),
    )
