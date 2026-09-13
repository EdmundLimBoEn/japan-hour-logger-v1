from __future__ import annotations

from datetime import datetime

from sqlalchemy import BigInteger, Boolean, DateTime, Float, ForeignKey, Integer, String, Text
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


class Receiver(Base):
    __tablename__ = "receivers"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    name: Mapped[str] = mapped_column(String(128))
    locator: Mapped[str | None] = mapped_column(String(8), nullable=True)
    timezone: Mapped[str] = mapped_column(String(64), default="Asia/Singapore")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))

    observations: Mapped[list["Observation"]] = relationship(back_populates="receiver")
    sessions: Mapped[list["Session"]] = relationship(back_populates="receiver")


class Session(Base):
    __tablename__ = "sessions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    receiver_id: Mapped[str] = mapped_column(String(64), ForeignKey("receivers.id"), index=True)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    instance_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    dial_frequency_hz: Mapped[int | None] = mapped_column(Integer, nullable=True)
    band: Mapped[str | None] = mapped_column(String(16), nullable=True)
    mode: Mapped[str | None] = mapped_column(String(16), nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)

    receiver: Mapped[Receiver] = relationship(back_populates="sessions")


class Station(Base):
    __tablename__ = "stations"

    callsign: Mapped[str] = mapped_column(String(32), primary_key=True)
    last_grid: Mapped[str | None] = mapped_column(String(8), nullable=True)
    grid_source: Mapped[str] = mapped_column(String(16), default="none")
    country: Mapped[str | None] = mapped_column(String(64), nullable=True)
    dxcc: Mapped[str | None] = mapped_column(String(16), nullable=True)
    continent: Mapped[str | None] = mapped_column(String(8), nullable=True)
    cqz: Mapped[int | None] = mapped_column(Integer, nullable=True)
    ituz: Mapped[int | None] = mapped_column(Integer, nullable=True)
    first_heard_utc: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_heard_utc: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, index=True)
    decode_count: Mapped[int] = mapped_column(Integer, default=0)
    last_snr_db: Mapped[float | None] = mapped_column(Float, nullable=True)
    last_distance_km: Mapped[float | None] = mapped_column(Float, nullable=True)


class Observation(Base):
    __tablename__ = "observations"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    receiver_id: Mapped[str] = mapped_column(String(64), ForeignKey("receivers.id"), index=True)
    session_id: Mapped[int | None] = mapped_column(Integer, ForeignKey("sessions.id"), nullable=True)
    timestamp_utc: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    timestamp_local: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    dial_frequency_hz: Mapped[int | None] = mapped_column(Integer, nullable=True)
    audio_frequency_hz: Mapped[int | None] = mapped_column(Integer, nullable=True)
    signal_frequency_hz: Mapped[int | None] = mapped_column(Integer, nullable=True)
    band: Mapped[str | None] = mapped_column(String(16), index=True)
    mode: Mapped[str] = mapped_column(String(16), default="FT8")
    snr_db: Mapped[float | None] = mapped_column(Float, nullable=True)
    dt: Mapped[float | None] = mapped_column(Float, nullable=True)
    df: Mapped[int | None] = mapped_column(Integer, nullable=True)
    raw_message: Mapped[str] = mapped_column(Text)
    message_type: Mapped[str] = mapped_column(String(32), default="unknown", index=True)
    is_cq: Mapped[bool] = mapped_column(Boolean, default=False)
    tx_callsign: Mapped[str | None] = mapped_column(String(32), nullable=True, index=True)
    rx_callsign: Mapped[str | None] = mapped_column(String(32), nullable=True)
    tx_grid: Mapped[str | None] = mapped_column(String(8), nullable=True)
    grid_source: Mapped[str] = mapped_column(String(16), default="none")
    country: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    dxcc: Mapped[str | None] = mapped_column(String(16), nullable=True, index=True)
    continent: Mapped[str | None] = mapped_column(String(8), nullable=True)
    cqz: Mapped[int | None] = mapped_column(Integer, nullable=True)
    ituz: Mapped[int | None] = mapped_column(Integer, nullable=True)
    distance_km: Mapped[float | None] = mapped_column(Float, nullable=True)
    bearing_deg: Mapped[float | None] = mapped_column(Float, nullable=True)
    low_confidence: Mapped[bool] = mapped_column(Boolean, default=False)
    off_air: Mapped[bool] = mapped_column(Boolean, default=False)
    source: Mapped[str] = mapped_column(String(16), default="udp")
    instance_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    fingerprint: Mapped[str] = mapped_column(String(256), index=True)
    raw_payload: Mapped[str | None] = mapped_column(Text, nullable=True)
    is_japan: Mapped[bool] = mapped_column(Boolean, default=False, index=True)

    receiver: Mapped[Receiver] = relationship(back_populates="observations")


class AppEvent(Base):
    __tablename__ = "app_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    timestamp_utc: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    level: Mapped[str] = mapped_column(String(16), default="info")
    event_type: Mapped[str] = mapped_column(String(64), index=True)
    message: Mapped[str] = mapped_column(Text)
    payload: Mapped[str | None] = mapped_column(Text, nullable=True)
    receiver_id: Mapped[str | None] = mapped_column(String(64), nullable=True)


class InputCursor(Base):
    __tablename__ = "input_cursors"

    receiver_id: Mapped[str] = mapped_column(String(64), ForeignKey("receivers.id"), primary_key=True)
    source_path: Mapped[str] = mapped_column(Text, primary_key=True)
    device: Mapped[int] = mapped_column(BigInteger)
    inode: Mapped[int] = mapped_column(BigInteger)
    generation: Mapped[str] = mapped_column(String(32))
    offset: Mapped[int] = mapped_column(BigInteger)
    anchor: Mapped[str] = mapped_column(Text)
