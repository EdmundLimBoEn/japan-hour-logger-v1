from __future__ import annotations

import math

from radio_logger.enrichment.maidenhead import grid_to_latlon, is_grid


def test_oj11_singapore_area():
    lat, lon = grid_to_latlon("OJ11")
    assert 1.0 <= lat <= 2.0
    assert 102.0 <= lon <= 104.0


def test_pm95_tokyo_area():
    lat, lon = grid_to_latlon("PM95")
    assert 35.0 <= lat <= 36.0
    assert 138.0 <= lon <= 140.0


def test_six_char_is_inside_four_char():
    lat4, lon4 = grid_to_latlon("PM95")
    lat6, lon6 = grid_to_latlon("PM95TQ")
    assert abs(lat6 - lat4) < 0.6
    assert abs(lon6 - lon4) < 1.2


def test_invalid_grid_rejected():
    assert not is_grid("RR73")
    assert not is_grid("TODO")
    assert not is_grid("PM")
    assert is_grid("fn20")
    assert is_grid("PM95tq")


def test_grid_never_required_to_be_even():
    lat, lon = grid_to_latlon("AA00")
    assert math.isclose(lat, -89.5)
    assert math.isclose(lon, -179.0)
