from __future__ import annotations

from datetime import datetime, timezone

from radio_logger.wsjtx.protocol import (
    MAGIC,
    DecodeMessage,
    HeartbeatMessage,
    StatusMessage,
    encode_close,
    encode_decode,
    encode_heartbeat,
    encode_status,
    parse_packet,
)


def test_decode_roundtrip():
    packet = encode_decode(
        "CQ JA1XYZ PM95",
        snr=-12,
        dt=0.2,
        df=1234,
        time_ms=15_000,
        instance_id="WSJT-X",
    )
    assert packet[:4] == MAGIC.to_bytes(4, "big")
    msg = parse_packet(packet, received_at=datetime(2026, 9, 11, 0, 0, tzinfo=timezone.utc))
    assert isinstance(msg, DecodeMessage)
    assert msg.message == "CQ JA1XYZ PM95"
    assert msg.snr == -12
    assert abs(msg.dt - 0.2) < 1e-9
    assert msg.df == 1234
    assert msg.mode == "FT8"
    assert msg.instance_id == "WSJT-X"
    assert msg.decode_time_utc is not None
    assert msg.decode_time_utc.second == 15


def test_heartbeat_and_status_and_close():
    hb = parse_packet(encode_heartbeat(version="2.7.0"))
    assert isinstance(hb, HeartbeatMessage)
    assert hb.version == "2.7.0"
    st = parse_packet(encode_status(dial_frequency_hz=14_074_000, de_grid="OJ11"))
    assert isinstance(st, StatusMessage)
    assert st.dial_frequency_hz == 14_074_000
    assert st.de_grid == "OJ11"
    close = parse_packet(encode_close())
    assert close.instance_id == "WSJT-X"


def test_bad_magic_raises():
    from radio_logger.wsjtx.protocol import PacketError
    import pytest

    with pytest.raises(PacketError):
        parse_packet(b"\x00\x00\x00\x00\x00\x00\x00\x02\x00\x00\x00\x02")
