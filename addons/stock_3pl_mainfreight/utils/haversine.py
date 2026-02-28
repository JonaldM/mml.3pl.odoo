# addons/stock_3pl_mainfreight/utils/haversine.py
from math import radians, cos, sin, asin, sqrt


def haversine_km(lat1, lon1, lat2, lon2):
    """Return the great-circle distance in kilometres between two points.

    Uses the haversine formula. Accurate to ~0.5% for distances < 10,000 km.
    All inputs in decimal degrees.
    """
    if not (-90 <= lat1 <= 90 and -90 <= lat2 <= 90):
        raise ValueError(
            f'Latitude out of range: lat1={lat1}, lat2={lat2}. Must be in [-90, 90].'
        )
    if not (-180 <= lon1 <= 180 and -180 <= lon2 <= 180):
        raise ValueError(
            f'Longitude out of range: lon1={lon1}, lon2={lon2}. Must be in [-180, 180].'
        )
    lat1, lon1, lat2, lon2 = map(radians, [lat1, lon1, lat2, lon2])
    dlat = lat2 - lat1
    dlon = lon2 - lon1
    a = sin(dlat / 2) ** 2 + cos(lat1) * cos(lat2) * sin(dlon / 2) ** 2
    return 6371 * 2 * asin(sqrt(a))


def sort_warehouses_by_distance(customer_lat, customer_lng, warehouses):
    """Sort a list of warehouse dicts by distance to the customer (closest first).

    Each dict must have 'lat' and 'lng' keys. Returns a NEW sorted list;
    does not modify the original.

    All warehouses in the input list must have valid lat/lng values — i.e.
    non-zero values from actual configuration, not the Odoo Float field
    default of 0.0. Warehouses with default 0.0 coordinates will produce
    silently incorrect distances and must be filtered out by the caller before
    passing them to this function.
    """
    return sorted(
        warehouses,
        key=lambda wh: haversine_km(customer_lat, customer_lng, wh['lat'], wh['lng']),
    )
