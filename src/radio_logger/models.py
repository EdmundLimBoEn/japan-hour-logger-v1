from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field

from radio_logger.timeutil import as_utc

GridSource = Literal["message", "cache", "external", "none"]
DecodeSource = Literal["udp", "all_txt", "jsonl", "simulator"]
MessageType = Literal[
    "cq",
    "cq_dx",
    "cq_pota",
    "cq_directed",
    "grid_exchange",
    "report",
    "r_report",
    "rrr",
    "rr73",
    "73",
    "contest",
    "free_text",
    "unknown",
]


class ParsedFt8(BaseModel):
    raw_message: str
    message_type: MessageType = "unknown"
    is_cq: bool = False
    tx_callsign: str | None = None
    rx_callsign: str | None = None
    tx_grid: str | None = None
    extra: dict[str, Any] = Field(default_factory=dict)


class RawDecode(BaseModel):
    source: DecodeSource
    instance_id: str | None = None
    decode_time_utc: datetime
    snr_db: float | None = None
    dt: float | None = None
    df: int | None = None
    mode: str = "FT8"
    raw_message: str
    low_confidence: bool = False
    off_air: bool = False
    is_new: bool | None = None
    dial_frequency_hz: int | None = None
    raw_payload: dict[str, Any] = Field(default_factory=dict)

    def fingerprint(self) -> str:
        fields = {
            "source": self.source,
            "instance_id": self.instance_id,
            "decode_time_utc": as_utc(self.decode_time_utc).isoformat(timespec="microseconds"),
            "snr_db": self.snr_db,
            "dt": self.dt,
            "df": self.df,
            "mode": self.mode,
            "raw_message": self.raw_message,
            "low_confidence": self.low_confidence,
            "off_air": self.off_air,
            "dial_frequency_hz": self.dial_frequency_hz,
        }
        payload = json.dumps(fields, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()


class NormalizedDecode(BaseModel):
    receiver_id: str
    session_id: int | None = None
    timestamp_utc: datetime
    timestamp_local: datetime
    dial_frequency_hz: int | None = None
    audio_frequency_hz: int | None = None
    signal_frequency_hz: int | None = None
    band: str | None = None
    mode: str = "FT8"
    snr_db: float | None = None
    dt: float | None = None
    df: int | None = None
    raw_message: str
    message_type: str = "unknown"
    is_cq: bool = False
    tx_callsign: str | None = None
    rx_callsign: str | None = None
    tx_grid: str | None = None
    grid_source: GridSource = "none"
    country: str | None = None
    dxcc: str | None = None
    continent: str | None = None
    cqz: int | None = None
    ituz: int | None = None
    distance_km: float | None = None
    bearing_deg: float | None = None
    low_confidence: bool = False
    off_air: bool = False
    source: DecodeSource
    instance_id: str | None = None
    fingerprint: str
    raw_payload: dict[str, Any] = Field(default_factory=dict)
    is_japan: bool = False


@dataclass
class ReceiverStatus:
    dial_frequency_hz: int | None = None
    mode: str | None = None
    de_call: str | None = None
    de_grid: str | None = None
    dx_call: str | None = None
    dx_grid: str | None = None
    decoding: bool = False
    transmitting: bool = False
    instance_id: str | None = None
    updated_at: datetime | None = None


@dataclass
class RuntimeState:
    started_at: datetime
    last_udp_at: datetime | None = None
    last_heartbeat_at: datetime | None = None
    last_decode_at: datetime | None = None
    last_clear_at: datetime | None = None
    last_close_at: datetime | None = None
    last_message: str | None = None
    decode_count_session: int = 0
    duplicate_suppressed: int = 0
    receiver_status: ReceiverStatus = field(default_factory=ReceiverStatus)
    receiver_statuses: dict[str, ReceiverStatus] = field(default_factory=dict)
    udp_bound: str | None = None
    last_error: str | None = None
