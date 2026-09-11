from radio_logger.ft8.callsign import extract_base_call, looks_like_callsign


def test_portable_and_compound_calls():
    assert looks_like_callsign("JA1XYZ/P")
    assert looks_like_callsign("KH2/W1ABC")
    assert looks_like_callsign("W1ABC/KH6")
    assert extract_base_call("JA1XYZ/P") == "JA1XYZ"
    assert extract_base_call("KH2/W1ABC") == "W1ABC"
    assert extract_base_call("W1ABC/KH6") == "W1ABC"
    assert not looks_like_callsign("CQ")
    assert not looks_like_callsign("PM95")
    assert not looks_like_callsign("FT8")
