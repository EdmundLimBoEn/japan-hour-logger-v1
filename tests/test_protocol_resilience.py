from __future__ import annotations

import asyncio
import random
import socket
import struct

import pytest

from radio_logger.config import UdpConfig
from radio_logger.wsjtx.listener import _Protocol, start_udp_listener
from radio_logger.wsjtx.protocol import (
    MAGIC,
    PacketError,
    StatusMessage,
    encode_decode,
    encode_heartbeat,
    encode_status,
    parse_packet,
)


def _utf8(value: str | None) -> bytes:
    if value is None:
        return struct.pack(">I", 0xFFFFFFFF)
    encoded = value.encode()
    return struct.pack(">I", len(encoded)) + encoded


def _improved_plus_status(schema: int = 2) -> bytes:
    # MessageClient::status_update in Improved PLUS 3.2.0 build 260908.
    return b"".join([
        struct.pack(">III", MAGIC, schema, 1), _utf8("WSJT-X - School"),
        struct.pack(">Q", 14_074_000), _utf8("FT8"), _utf8("JA1XYZ"),
        _utf8("-12"), _utf8("FT8"), struct.pack(">???II", False, False, True, 1500, 1500),
        _utf8("9V1XX"), _utf8("OJ11"), _utf8("PM95"),
        struct.pack(">?", False), _utf8(None), struct.pack(">?BII", False, 0, 0xFFFFFFFF, 15),
        _utf8("School 日本"), _utf8("CQ 9V1XX OJ11"), _utf8("1234567" * 12),
    ])


@pytest.mark.parametrize("schema", [2, 3])
def test_improved_plus_320_status_includes_unknown_tx_symbols(schema):
    status = parse_packet(_improved_plus_status(schema))
    assert isinstance(status, StatusMessage)
    assert status.schema == schema
    assert status.dial_frequency_hz == 14_074_000
    assert status.configuration_name == "School 日本"
    assert status.tx_message == "CQ 9V1XX OJ11"
    assert status.tr_period == 15
    assert status.frequency_tolerance == 0xFFFFFFFF
    assert status.sub_mode is None


def test_status_audio_offsets_are_unsigned_qt_values():
    packet = _improved_plus_status().replace(struct.pack(">II", 1500, 1500), struct.pack(">II", 0xFFFFFFFF, 0x80000000))
    status = parse_packet(packet)
    assert status.rx_df == 0xFFFFFFFF
    assert status.tx_df == 0x80000000


def test_damaged_packets_raise_only_packet_errors():
    packets = [encode_heartbeat(), encode_status(), encode_decode("CQ JA1XYZ PM95")]
    damaged = [packet[:length] for packet in packets for length in range(len(packet))]
    randomizer = random.Random(47)
    for _ in range(1000):
        packet = bytearray(randomizer.choice(packets))
        offset = randomizer.randrange(len(packet))
        packet[offset] = randomizer.randrange(256)
        damaged.append(bytes(packet))
    for packet in damaged:
        try:
            parse_packet(packet)
        except PacketError:
            pass


def test_socket_error_does_not_claim_connection_lost():
    errors = []
    closed = []
    protocol = _Protocol(lambda *_: None, errors.append, closed.append)
    problem = OSError("temporary receive error")
    protocol.error_received(problem)
    assert errors == [problem]
    assert closed == []
    protocol.connection_lost(problem)
    assert closed == [problem]


@pytest.mark.asyncio
async def test_callback_failure_does_not_stop_next_datagram():
    completed = asyncio.get_running_loop().create_future()

    def receive(data, _addr):
        if data == b"bad":
            raise ValueError("bad callback")
        completed.set_result(data)

    closed = asyncio.get_running_loop().create_future()
    transport, bound = await start_udp_listener(
        UdpConfig(port=0), receive, on_connection_lost=closed.set_result,
    )
    try:
        address = transport.get_extra_info("sockname")
        assert bound == f"{address[0]}:{address[1]}"
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sender:
            sender.sendto(b"bad", address)
            sender.sendto(b"good", address)
        assert await asyncio.wait_for(completed, 2) == b"good"
    finally:
        transport.close()
        assert await asyncio.wait_for(closed, 2) is None


@pytest.mark.asyncio
async def test_endpoint_creation_failure_closes_socket(monkeypatch):
    loop = asyncio.get_running_loop()
    created = []

    async def fail_endpoint(_factory, *, sock):
        created.append(sock)
        raise OSError("endpoint creation failed")

    monkeypatch.setattr(loop, "create_datagram_endpoint", fail_endpoint)
    with pytest.raises(OSError, match="endpoint creation failed"):
        await start_udp_listener(UdpConfig(port=0), lambda *_: None)
    assert created[0].fileno() == -1


@pytest.mark.asyncio
async def test_endpoint_cancellation_closes_socket(monkeypatch):
    loop = asyncio.get_running_loop()
    created = []

    async def cancelled_endpoint(_factory, *, sock):
        created.append(sock)
        raise asyncio.CancelledError

    monkeypatch.setattr(loop, "create_datagram_endpoint", cancelled_endpoint)
    with pytest.raises(asyncio.CancelledError):
        await start_udp_listener(UdpConfig(port=0), lambda *_: None)
    assert created[0].fileno() == -1


@pytest.mark.asyncio
async def test_improved_plus_status_and_decode_persist_after_bad_udp(ingestor):
    completed = asyncio.get_running_loop().create_future()

    def receive(data, address):
        row = ingestor.handle_datagram(data, address)
        if row is not None:
            completed.set_result(row)

    transport, _bound = await start_udp_listener(UdpConfig(port=0), receive)
    try:
        address = transport.get_extra_info("sockname")
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sender:
            sender.sendto(b"malformed UDP", address)
            sender.sendto(_improved_plus_status(schema=3), address)
            sender.sendto(encode_decode("CQ JA1XYZ PM95", mode="~", instance_id="WSJT-X - School"), address)
        row = await asyncio.wait_for(completed, 2)
        assert row.mode == "FT8"
        assert row.dial_frequency_hz == 14_074_000
        assert row.tx_callsign == "JA1XYZ"
        assert ingestor.runtime.parse_errors == 1
    finally:
        transport.close()
        await asyncio.sleep(0)
