from __future__ import annotations

from datetime import datetime, timezone
import math
import struct

import pytest

from radio_logger.wsjtx.protocol import (
    MAGIC,
    DecodeMessage,
    HeartbeatMessage,
    StatusMessage,
    PacketError,
    UnknownMessage,
    encode_close,
    encode_decode,
    encode_heartbeat,
    encode_status,
    encode_header,
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


@pytest.mark.parametrize("schema", [2, 3])
def test_supported_schemas_ignore_appended_fields(schema):
    packet = bytearray(encode_decode("CQ JA1XYZ PM95", mode="~"))
    packet[4:8] = struct.pack(">I", schema)
    parsed = parse_packet(bytes(packet) + b"future extension")
    assert parsed.schema == schema
    assert parsed.mode == "~"


@pytest.mark.parametrize("schema", [0, 1, 4, 0xFFFFFFFF])
def test_unsupported_schema_is_rejected(schema):
    packet = encode_header(6, "WSJT-X", schema=schema).dumps()
    with pytest.raises(PacketError, match="schema"):
        parse_packet(packet)


@pytest.mark.parametrize("type_id", [0, 1, 2, 3, 6])
def test_missing_instance_id_is_rejected(type_id):
    with pytest.raises(PacketError):
        parse_packet(struct.pack(">III", MAGIC, 2, type_id))


@pytest.mark.parametrize("suffix", [b"", b"\0", b"\0" * 7])
def test_status_requires_complete_dial_frequency(suffix):
    with pytest.raises(PacketError):
        parse_packet(encode_header(1, "WSJT-X").dumps() + suffix)


@pytest.mark.parametrize("suffix", [b"\0", b"\0\0", b"\0\0\0"])
def test_partial_optional_status_field_is_rejected(suffix):
    packet = encode_status() + b"\0" + suffix
    with pytest.raises(PacketError):
        parse_packet(packet)


@pytest.mark.parametrize("suffix", [b"\0", b"\0\0", b"\0\0\0"])
def test_partial_heartbeat_tail_is_rejected(suffix):
    with pytest.raises(PacketError):
        parse_packet(encode_header(0, "WSJT-X").dumps() + suffix)


def test_legacy_heartbeat_and_decode_optional_fields():
    assert parse_packet(encode_header(0, "WSJT-X").dumps()).max_schema is None
    decoded = parse_packet(encode_decode("CQ JA1XYZ PM95")[:-2])
    assert not decoded.off_air
    assert not decoded.low_confidence


@pytest.mark.parametrize("dt", [math.nan, math.inf, -math.inf])
def test_nonfinite_delta_time_is_rejected(dt):
    with pytest.raises(PacketError, match="finite"):
        parse_packet(encode_decode("CQ JA1XYZ PM95", dt=dt))


@pytest.mark.parametrize("time_ms", [86_400_000, 0xFFFFFFFF])
def test_invalid_qtime_is_rejected(time_ms):
    with pytest.raises(PacketError, match="QTime"):
        parse_packet(encode_decode("CQ JA1XYZ PM95", time_ms=time_ms))


def test_decode_crosses_utc_midnight():
    received = datetime(2026, 9, 14, 0, 0, 1, tzinfo=timezone.utc)
    parsed = parse_packet(encode_decode("CQ JA1XYZ PM95", time_ms=86_385_000), received)
    assert parsed.decode_time_utc == datetime(2026, 9, 13, 23, 59, 45, tzinfo=timezone.utc)


def test_invalid_utf8_is_rejected():
    packet = encode_decode("CQ JA1XYZ PM95").replace(b"JA1XYZ", b"\xffA1XYZ")
    with pytest.raises(PacketError, match="UTF-8"):
        parse_packet(packet)


def test_invalid_bool_is_rejected():
    packet = bytearray(encode_decode("CQ JA1XYZ PM95"))
    packet[-1] = 255
    with pytest.raises(PacketError, match="boolean"):
        parse_packet(bytes(packet))


def test_unknown_message_is_ignored():
    parsed = parse_packet(encode_header(99, "WSJT-X").dumps() + b"unknown data")
    assert isinstance(parsed, UnknownMessage)
    assert parsed.type_id == 99


def test_zero_dial_is_unknown():
    assert parse_packet(encode_status(dial_frequency_hz=0)).dial_frequency_hz is None


def test_oversized_dial_does_not_replace_last_valid_receiver_state(ingestor):
    ingestor.handle_datagram(encode_status(dial_frequency_hz=14_074_000))
    invalid = encode_status(dial_frequency_hz=2**63)
    with pytest.raises(PacketError, match="frequency"):
        parse_packet(invalid)
    ingestor.handle_datagram(invalid)
    assert ingestor.runtime.receiver_status.dial_frequency_hz == 14_074_000
