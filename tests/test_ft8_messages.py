from __future__ import annotations

import pytest

from radio_logger.ft8.message import parse_ft8_message

CASES = [
    ("CQ JA1XYZ PM95", "cq", "JA1XYZ", None, "PM95", True),
    ("CQ DX JH8QRP QN03", "cq_dx", "JH8QRP", None, "QN03", True),
    ("CQ POTA JR3ABC PM74", "cq_pota", "JR3ABC", None, "PM74", True),
    ("CQ JA 7K1AAA PM95", "cq_directed", "7K1AAA", None, "PM95", True),
    ("CQ TEST JA1XYZ PM95", "cq_directed", "JA1XYZ", None, "PM95", True),
    ("9V1XX JA1XYZ PM95", "grid_exchange", "JA1XYZ", "9V1XX", "PM95", False),
    ("9V1XX JA1XYZ R-10", "r_report", "JA1XYZ", "9V1XX", None, False),
    ("9V1XX JA1XYZ -09", "report", "JA1XYZ", "9V1XX", None, False),
    ("9V1XX JA1XYZ RR73", "rr73", "JA1XYZ", "9V1XX", None, False),
    ("9V1XX JA1XYZ RRR", "rrr", "JA1XYZ", "9V1XX", None, False),
    ("9V1XX JA1XYZ 73", "73", "JA1XYZ", "9V1XX", None, False),
    ("<...> JA1XYZ PM95", "grid_exchange", "JA1XYZ", None, "PM95", False),
    ("CQ JA1XYZ/P PM95", "cq", "JA1XYZ/P", None, "PM95", True),
    ("CQ KH2/W1ABC QK23", "cq", "KH2/W1ABC", None, "QK23", True),
    ("W1ABC JA1XYZ 599", "contest", "JA1XYZ", "W1ABC", None, False),
    ("this is not ft8", "unknown", None, None, None, False),
    ("!!! garbled ???", "unknown", None, None, None, False),
    ("", "unknown", None, None, None, False),
]


@pytest.mark.parametrize("raw,kind,tx,rx,grid,cq", CASES)
def test_ft8_messages(raw, kind, tx, rx, grid, cq):
    parsed = parse_ft8_message(raw)
    assert parsed.message_type == kind
    assert parsed.tx_callsign == tx
    assert parsed.rx_callsign == rx
    assert parsed.tx_grid == grid
    assert parsed.is_cq is cq


def test_malformed_never_raises():
    parse_ft8_message(None)  # type: ignore[arg-type]
    parse_ft8_message("\x00\x01")
    parse_ft8_message("CQ")
