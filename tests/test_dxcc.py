from __future__ import annotations

import pytest

from radio_logger.enrichment.dxcc import DxccLookup, default_lookup

LOOKUP = default_lookup()

CASES = [
    ("9V1XX", "Singapore", "9V"),
    ("JA1XYZ", "Japan", "JA"),
    ("JH8ABC", "Japan", "JA"),
    ("7K1AAA", "Japan", "JA"),
    ("8J1HAM", "Japan", "JA"),
    ("VK2ABC", "Australia", "VK"),
    ("YB1HDR", "Indonesia", "YB"),
    ("BY1RX", "China", "BY"),
    ("BA1CW", "China", "BY"),
    ("HL2XYZ", "Republic of Korea", "HL"),
    ("HS0ZAA", "Thailand", "HS"),
    ("DU1ABC", "Philippines", "DU"),
    ("W1AW", "United States", "K"),
    ("K1JT", "United States", "K"),
    ("N0CALL", "United States", "K"),
    ("JA1XYZ/P", "Japan", "JA"),
    ("JA1XYZ/1", "Japan", "JA"),
    ("KH2/W1ABC", "Guam", "KH2"),
    ("W1ABC/KH6", "Hawaii", "KH6"),
]


@pytest.mark.parametrize("call,country,prefix", CASES)
def test_dxcc_prefixes(call: str, country: str, prefix: str) -> None:
    match = LOOKUP.lookup(call)
    assert match is not None, call
    assert match.entity_name == country, (call, match)
    assert match.primary_prefix == prefix


def test_unknown_does_not_crash():
    assert LOOKUP.lookup("...") is None
    assert LOOKUP.lookup("") is None
    assert LOOKUP.lookup(None) is None


def test_lookup_is_cached_singleton():
    assert default_lookup() is default_lookup()
    assert isinstance(LOOKUP, DxccLookup)
