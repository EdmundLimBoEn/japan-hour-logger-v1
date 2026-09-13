from __future__ import annotations

import math
import struct
from dataclasses import dataclass, field
from datetime import datetime, time, timezone
from enum import IntEnum
from typing import Any

MAGIC = 0xADBCCBDA
UTC = timezone.utc


class MessageTypeId(IntEnum):
    HEARTBEAT = 0
    STATUS = 1
    DECODE = 2
    CLEAR = 3
    REPLY = 4
    QSO_LOGGED = 5
    CLOSE = 6
    REPLAY = 7
    HALT_TX = 8
    FREE_TEXT = 9
    WSPR_DECODE = 10
    LOCATION = 11
    LOGGED_ADIF = 12
    HIGHLIGHT_CALLSIGN = 13


@dataclass
class HeartbeatMessage:
    instance_id: str
    max_schema: int | None = None
    version: str | None = None
    revision: str | None = None
    schema: int = 2
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass
class StatusMessage:
    instance_id: str
    dial_frequency_hz: int | None = None
    mode: str | None = None
    dx_call: str | None = None
    report: str | None = None
    tx_mode: str | None = None
    tx_enabled: bool = False
    transmitting: bool = False
    decoding: bool = False
    rx_df: int | None = None
    tx_df: int | None = None
    de_call: str | None = None
    de_grid: str | None = None
    dx_grid: str | None = None
    tx_watchdog: bool = False
    sub_mode: str | None = None
    fast_mode: bool = False
    special_op_mode: int | None = None
    frequency_tolerance: int | None = None
    tr_period: int | None = None
    configuration_name: str | None = None
    tx_message: str | None = None
    schema: int = 2
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass
class DecodeMessage:
    instance_id: str
    is_new: bool
    time_ms: int
    snr: int
    dt: float
    df: int
    mode: str
    message: str
    low_confidence: bool = False
    off_air: bool = False
    schema: int = 2
    decode_time_utc: datetime | None = None
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass
class ClearMessage:
    instance_id: str
    window: int | None = None
    schema: int = 2
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass
class CloseMessage:
    instance_id: str
    schema: int = 2
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass
class UnknownMessage:
    instance_id: str | None
    type_id: int
    schema: int
    raw: dict[str, Any] = field(default_factory=dict)


WsjtxMessage = (
    HeartbeatMessage
    | StatusMessage
    | DecodeMessage
    | ClearMessage
    | CloseMessage
    | UnknownMessage
)


class PacketError(ValueError):
    pass


class _Reader:
    def __init__(self, data: bytes):
        self.data = data
        self.offset = 0

    def remaining(self) -> int:
        return len(self.data) - self.offset

    def take(self, n: int) -> bytes:
        if self.remaining() < n:
            raise PacketError("truncated WSJT-X packet")
        chunk = self.data[self.offset : self.offset + n]
        self.offset += n
        return chunk

    def u8(self) -> int:
        return self.take(1)[0]

    def bool(self) -> bool:
        value = self.u8()
        if value not in (0, 1):
            raise PacketError("invalid WSJT-X boolean")
        return bool(value)

    def u32(self) -> int:
        return struct.unpack(">I", self.take(4))[0]

    def i32(self) -> int:
        return struct.unpack(">i", self.take(4))[0]

    def u64(self) -> int:
        return struct.unpack(">Q", self.take(8))[0]

    def f64(self) -> float:
        value = struct.unpack(">d", self.take(8))[0]
        if not math.isfinite(value):
            raise PacketError("WSJT-X floating point value must be finite")
        return value

    def utf8(self) -> str | None:
        length = self.u32()
        if length == 0xFFFFFFFF:
            return None
        if length == 0:
            return ""
        try:
            return self.take(length).decode("utf-8")
        except UnicodeDecodeError as exc:
            raise PacketError("invalid WSJT-X UTF-8 string") from exc


class _Writer:
    def __init__(self):
        self.parts: list[bytes] = []

    def u32(self, value: int) -> None:
        self.parts.append(struct.pack(">I", value & 0xFFFFFFFF))

    def i32(self, value: int) -> None:
        self.parts.append(struct.pack(">i", int(value)))

    def u64(self, value: int) -> None:
        self.parts.append(struct.pack(">Q", int(value)))

    def f64(self, value: float) -> None:
        self.parts.append(struct.pack(">d", float(value)))

    def u8(self, value: int) -> None:
        self.parts.append(bytes([int(value) & 0xFF]))

    def boolean(self, value: bool) -> None:
        self.u8(1 if value else 0)

    def utf8(self, value: str | None) -> None:
        if value is None:
            self.u32(0xFFFFFFFF)
            return
        data = value.encode("utf-8")
        self.u32(len(data))
        self.parts.append(data)

    def dumps(self) -> bytes:
        return b"".join(self.parts)


def parse_packet(data: bytes, received_at: datetime | None = None) -> WsjtxMessage:
    if len(data) < 12:
        raise PacketError("packet shorter than WSJT-X header")
    reader = _Reader(data)
    magic = reader.u32()
    if magic != MAGIC:
        raise PacketError(f"bad magic 0x{magic:08x}")
    schema = reader.u32()
    if schema not in (2, 3):
        raise PacketError(f"unsupported WSJT-X schema {schema}; expected 2 or 3")
    type_id = reader.u32()
    instance_id = _required_utf8(reader, "instance ID")
    now = received_at or datetime.now(tz=UTC)

    if type_id == MessageTypeId.HEARTBEAT:
        max_schema = reader.u32() if reader.remaining() else None
        version = _opt_utf8(reader)
        revision = _opt_utf8(reader)
        return HeartbeatMessage(
            instance_id=instance_id,
            max_schema=max_schema,
            version=version,
            revision=revision,
            schema=schema,
            raw={"type": "heartbeat", "schema": schema},
        )

    if type_id == MessageTypeId.STATUS:
        msg = StatusMessage(instance_id=instance_id, schema=schema)
        dial_frequency = reader.u64()
        if dial_frequency > 2**63 - 1:
            raise PacketError("WSJT-X dial frequency exceeds signed 64-bit storage")
        msg.dial_frequency_hz = dial_frequency or None
        msg.mode = _required_utf8(reader, "status mode")
        msg.dx_call = _opt_utf8(reader)
        msg.report = _opt_utf8(reader)
        msg.tx_mode = _opt_utf8(reader)
        if reader.remaining() >= 1:
            msg.tx_enabled = reader.bool()
        if reader.remaining() >= 1:
            msg.transmitting = reader.bool()
        if reader.remaining() >= 1:
            msg.decoding = reader.bool()
        if reader.remaining():
            msg.rx_df = reader.u32()
        if reader.remaining():
            msg.tx_df = reader.u32()
        msg.de_call = _opt_utf8(reader)
        msg.de_grid = _opt_utf8(reader)
        msg.dx_grid = _opt_utf8(reader)
        if reader.remaining() >= 1:
            msg.tx_watchdog = reader.bool()
        msg.sub_mode = _opt_utf8(reader)
        if reader.remaining() >= 1:
            msg.fast_mode = reader.bool()
        if reader.remaining() >= 1:
            msg.special_op_mode = reader.u8()
        if reader.remaining():
            msg.frequency_tolerance = reader.u32()
        if reader.remaining():
            msg.tr_period = reader.u32()
        msg.configuration_name = _opt_utf8(reader)
        msg.tx_message = _opt_utf8(reader)
        msg.raw = {"type": "status", "schema": schema}
        return msg

    if type_id == MessageTypeId.DECODE:
        is_new = reader.bool()
        time_ms = reader.u32()
        snr = reader.i32()
        dt = reader.f64()
        df = reader.u32()
        mode = _required_utf8(reader, "mode")
        message = _required_utf8(reader, "message")
        low_confidence = reader.bool() if reader.remaining() >= 1 else False
        off_air = reader.bool() if reader.remaining() >= 1 else False
        decode_time = _qtime_to_datetime(now, time_ms)
        return DecodeMessage(
            instance_id=instance_id,
            is_new=is_new,
            time_ms=time_ms,
            snr=snr,
            dt=dt,
            df=int(df),
            mode=mode,
            message=message,
            low_confidence=low_confidence,
            off_air=off_air,
            schema=schema,
            decode_time_utc=decode_time,
            raw={"type": "decode", "schema": schema, "is_new": is_new, "mode": mode},
        )

    if type_id == MessageTypeId.CLEAR:
        window = reader.u8() if reader.remaining() >= 1 else None
        return ClearMessage(instance_id=instance_id, window=window, schema=schema, raw={"type": "clear"})

    if type_id == MessageTypeId.CLOSE:
        return CloseMessage(instance_id=instance_id, schema=schema, raw={"type": "close"})

    return UnknownMessage(
        instance_id=instance_id,
        type_id=type_id,
        schema=schema,
        raw={"type": f"unknown_{type_id}"},
    )


def _opt_utf8(reader: _Reader) -> str | None:
    if not reader.remaining():
        return None
    return reader.utf8()


def _required_utf8(reader: _Reader, field_name: str) -> str:
    if reader.remaining() < 4:
        raise PacketError(f"WSJT-X packet missing {field_name}")
    value = reader.utf8()
    if not value:
        raise PacketError(f"WSJT-X packet missing {field_name}")
    return value


def _qtime_to_datetime(now: datetime, millis: int) -> datetime:
    if not 0 <= millis < 24 * 60 * 60 * 1000:
        raise PacketError(f"invalid QTime milliseconds: {millis}")
    now = now.astimezone(UTC) if now.tzinfo else now.replace(tzinfo=UTC)
    seconds, milli = divmod(int(millis), 1000)
    hours, rem = divmod(seconds, 3600)
    minutes, secs = divmod(rem, 60)
    decoded = datetime.combine(now.date(), time(hours, minutes, secs, milli * 1000), tzinfo=UTC)
    # Around UTC midnight the QTime day may belong to yesterday or tomorrow.
    delta = (decoded - now).total_seconds()
    if delta > 12 * 3600:
        from datetime import timedelta

        decoded -= timedelta(days=1)
    elif delta < -12 * 3600:
        from datetime import timedelta

        decoded += timedelta(days=1)
    return decoded


def encode_header(type_id: int, instance_id: str, schema: int = 2) -> _Writer:
    writer = _Writer()
    writer.u32(MAGIC)
    writer.u32(schema)
    writer.u32(type_id)
    writer.utf8(instance_id)
    return writer


def encode_heartbeat(
    instance_id: str = "WSJT-X",
    max_schema: int = 3,
    version: str = "2.7.0",
    revision: str = "test",
) -> bytes:
    writer = encode_header(MessageTypeId.HEARTBEAT, instance_id)
    writer.u32(max_schema)
    writer.utf8(version)
    writer.utf8(revision)
    return writer.dumps()


def encode_decode(
    message: str,
    *,
    instance_id: str = "WSJT-X",
    is_new: bool = True,
    time_ms: int = 12 * 3600 * 1000,
    snr: int = -12,
    dt: float = 0.2,
    df: int = 1234,
    mode: str = "FT8",
    low_confidence: bool = False,
    off_air: bool = False,
) -> bytes:
    writer = encode_header(MessageTypeId.DECODE, instance_id)
    writer.boolean(is_new)
    writer.u32(time_ms)
    writer.i32(snr)
    writer.f64(dt)
    writer.u32(df)
    writer.utf8(mode)
    writer.utf8(message)
    writer.boolean(low_confidence)
    writer.boolean(off_air)
    return writer.dumps()


def encode_status(
    *,
    instance_id: str = "WSJT-X",
    dial_frequency_hz: int = 14_074_000,
    mode: str = "FT8",
    de_call: str = "9V1XX",
    de_grid: str = "OJ11",
) -> bytes:
    writer = encode_header(MessageTypeId.STATUS, instance_id)
    writer.u64(dial_frequency_hz)
    writer.utf8(mode)
    writer.utf8("")
    writer.utf8("")
    writer.utf8(mode)
    writer.boolean(False)
    writer.boolean(False)
    writer.boolean(False)
    writer.i32(0)
    writer.i32(0)
    writer.utf8(de_call)
    writer.utf8(de_grid)
    writer.utf8("")
    writer.boolean(False)
    writer.utf8("")
    writer.boolean(False)
    return writer.dumps()


def encode_clear(instance_id: str = "WSJT-X") -> bytes:
    return encode_header(MessageTypeId.CLEAR, instance_id).dumps()


def encode_close(instance_id: str = "WSJT-X") -> bytes:
    return encode_header(MessageTypeId.CLOSE, instance_id).dumps()
