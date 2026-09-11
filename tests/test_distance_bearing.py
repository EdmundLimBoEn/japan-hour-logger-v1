from __future__ import annotations

from radio_logger.enrichment.bearing import bearing_deg
from radio_logger.enrichment.distance import haversine_km
from radio_logger.enrichment.maidenhead import grid_to_latlon


def test_singapore_to_tokyo_distance():
    s_lat, s_lon = grid_to_latlon("OJ11")
    j_lat, j_lon = grid_to_latlon("PM95")
    km = haversine_km(s_lat, s_lon, j_lat, j_lon)
    assert 5000 < km < 5600


def test_zero_distance():
    lat, lon = grid_to_latlon("OJ11")
    assert haversine_km(lat, lon, lat, lon) == 0


def test_bearing_north():
    bearing = bearing_deg(1.0, 103.0, 2.0, 103.0)
    assert 0 <= bearing < 2 or bearing > 358


def test_bearing_singapore_to_japan_northeast():
    s_lat, s_lon = grid_to_latlon("OJ11")
    j_lat, j_lon = grid_to_latlon("PM95")
    bearing = bearing_deg(s_lat, s_lon, j_lat, j_lon)
    assert 20 < bearing < 50
