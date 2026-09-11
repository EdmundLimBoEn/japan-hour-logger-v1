from __future__ import annotations

import json
from datetime import datetime
from typing import Any

from sqlalchemy import Select, func, select
from sqlalchemy.orm import Session

from radio_logger.database.models import AppEvent, Observation, Receiver, Session as RxSession, Station
from radio_logger.ft8.callsign import extract_base_call
from radio_logger.models import NormalizedDecode


class Repository:
    def __init__(self, session: Session):
        self.session = session

    def upsert_receiver(self, receiver_id: str, name: str, locator: str | None, timezone_name: str) -> Receiver:
        from datetime import timezone as tz

        row = self.session.get(Receiver, receiver_id)
        now = datetime.now(tz=tz.utc)
        if row is None:
            row = Receiver(
                id=receiver_id,
                name=name,
                locator=locator,
                timezone=timezone_name,
                created_at=now,
                updated_at=now,
            )
            self.session.add(row)
        else:
            row.name = name
            row.locator = locator
            row.timezone = timezone_name
            row.updated_at = now
        self.session.flush()
        return row

    def open_session(
        self,
        receiver_id: str,
        *,
        instance_id: str | None = None,
        dial_frequency_hz: int | None = None,
        band: str | None = None,
        mode: str | None = None,
    ) -> RxSession:
        from datetime import timezone

        row = RxSession(
            receiver_id=receiver_id,
            started_at=datetime.now(tz=timezone.utc),
            instance_id=instance_id,
            dial_frequency_hz=dial_frequency_hz,
            band=band,
            mode=mode,
        )
        self.session.add(row)
        self.session.flush()
        return row

    def insert_observation(self, decoded: NormalizedDecode, session_id: int | None = None) -> Observation:
        row = Observation(
            receiver_id=decoded.receiver_id,
            session_id=session_id if session_id is not None else decoded.session_id,
            timestamp_utc=decoded.timestamp_utc,
            timestamp_local=decoded.timestamp_local,
            dial_frequency_hz=decoded.dial_frequency_hz,
            audio_frequency_hz=decoded.audio_frequency_hz,
            signal_frequency_hz=decoded.signal_frequency_hz,
            band=decoded.band,
            mode=decoded.mode,
            snr_db=decoded.snr_db,
            dt=decoded.dt,
            df=decoded.df,
            raw_message=decoded.raw_message,
            message_type=decoded.message_type,
            is_cq=decoded.is_cq,
            tx_callsign=decoded.tx_callsign,
            rx_callsign=decoded.rx_callsign,
            tx_grid=decoded.tx_grid,
            grid_source=decoded.grid_source,
            country=decoded.country,
            dxcc=decoded.dxcc,
            continent=decoded.continent,
            cqz=decoded.cqz,
            ituz=decoded.ituz,
            distance_km=decoded.distance_km,
            bearing_deg=decoded.bearing_deg,
            low_confidence=decoded.low_confidence,
            off_air=decoded.off_air,
            source=decoded.source,
            instance_id=decoded.instance_id,
            fingerprint=decoded.fingerprint,
            raw_payload=json.dumps(decoded.raw_payload) if decoded.raw_payload else None,
            is_japan=decoded.is_japan,
        )
        self.session.add(row)
        self._touch_station(decoded)
        self.session.flush()
        return row

    def _touch_station(self, decoded: NormalizedDecode) -> None:
        if not decoded.tx_callsign:
            return
        key = extract_base_call(decoded.tx_callsign)
        station = self.session.get(Station, key)
        if station is None:
            station = Station(callsign=key, decode_count=0, first_heard_utc=decoded.timestamp_utc)
            self.session.add(station)
        if decoded.tx_grid and (decoded.grid_source == "message" or station.last_grid is None):
            station.last_grid = decoded.tx_grid
            station.grid_source = decoded.grid_source
        if decoded.country:
            station.country = decoded.country
        if decoded.dxcc:
            station.dxcc = decoded.dxcc
        if decoded.continent:
            station.continent = decoded.continent
        if decoded.cqz is not None:
            station.cqz = decoded.cqz
        if decoded.ituz is not None:
            station.ituz = decoded.ituz
        station.last_heard_utc = decoded.timestamp_utc
        station.first_heard_utc = station.first_heard_utc or decoded.timestamp_utc
        station.decode_count = (station.decode_count or 0) + 1
        station.last_snr_db = decoded.snr_db
        station.last_distance_km = decoded.distance_km

    def add_event(
        self,
        event_type: str,
        message: str,
        *,
        level: str = "info",
        payload: dict[str, Any] | None = None,
        receiver_id: str | None = None,
    ) -> AppEvent:
        from datetime import timezone

        row = AppEvent(
            timestamp_utc=datetime.now(tz=timezone.utc),
            level=level,
            event_type=event_type,
            message=message,
            payload=json.dumps(payload) if payload else None,
            receiver_id=receiver_id,
        )
        self.session.add(row)
        return row

    def observation_query(
        self,
        *,
        receiver_id: str | None = None,
        callsign: str | None = None,
        country: str | None = None,
        japan_only: bool = False,
        since: datetime | None = None,
        until: datetime | None = None,
    ) -> Select[tuple[Observation]]:
        stmt = select(Observation)
        if receiver_id:
            stmt = stmt.where(Observation.receiver_id == receiver_id)
        if callsign:
            stmt = stmt.where(Observation.tx_callsign == callsign.upper())
        if country:
            stmt = stmt.where(Observation.country == country)
        if japan_only:
            stmt = stmt.where(Observation.is_japan.is_(True))
        if since:
            stmt = stmt.where(Observation.timestamp_utc >= since)
        if until:
            stmt = stmt.where(Observation.timestamp_utc < until)
        return stmt

    def list_observations(
        self,
        *,
        limit: int = 50,
        offset: int = 0,
        **filters: Any,
    ) -> tuple[list[Observation], int]:
        stmt = self.observation_query(**filters)
        total = self.session.scalar(select(func.count()).select_from(stmt.subquery())) or 0
        rows = list(
            self.session.scalars(stmt.order_by(Observation.timestamp_utc.desc(), Observation.id.desc()).offset(offset).limit(limit))
        )
        return rows, int(total)

    def latest_observations(self, limit: int = 25, receiver_id: str | None = None) -> list[Observation]:
        stmt = select(Observation).order_by(Observation.timestamp_utc.desc(), Observation.id.desc()).limit(limit)
        if receiver_id:
            stmt = stmt.where(Observation.receiver_id == receiver_id)
        return list(self.session.scalars(stmt))

    def count_since(self, since: datetime, *, japan_only: bool = False, receiver_id: str | None = None) -> int:
        stmt = select(func.count()).select_from(Observation).where(Observation.timestamp_utc >= since)
        if japan_only:
            stmt = stmt.where(Observation.is_japan.is_(True))
        if receiver_id:
            stmt = stmt.where(Observation.receiver_id == receiver_id)
        return int(self.session.scalar(stmt) or 0)

    def station_get(self, callsign: str) -> Station | None:
        return self.session.get(Station, extract_base_call(callsign.upper()))

    def list_stations(self, *, limit: int = 50, offset: int = 0, japan_only: bool = False) -> tuple[list[Station], int]:
        stmt = select(Station)
        if japan_only:
            stmt = stmt.where(Station.country.in_(["Japan", "Ogasawara", "Minami Torishima"]))
        total = self.session.scalar(select(func.count()).select_from(stmt.subquery())) or 0
        rows = list(
            self.session.scalars(stmt.order_by(Station.last_heard_utc.desc()).offset(offset).limit(limit))
        )
        return rows, int(total)

    def station_history(self, callsign: str, *, limit: int = 100) -> list[Observation]:
        key = callsign.upper()
        base = extract_base_call(key)
        stmt = (
            select(Observation)
            .where((Observation.tx_callsign == key) | (Observation.tx_callsign.like(f"%{base}%")))
            .order_by(Observation.timestamp_utc.desc())
            .limit(limit)
        )
        return list(self.session.scalars(stmt))
