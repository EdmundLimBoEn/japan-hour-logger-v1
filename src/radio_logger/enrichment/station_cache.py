from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

from radio_logger.ft8.callsign import extract_base_call


@dataclass
class CachedStation:
    callsign: str
    grid: str | None = None
    grid_source: str = "none"
    country: str | None = None
    dxcc: str | None = None
    continent: str | None = None
    cqz: int | None = None
    ituz: int | None = None
    first_heard_utc: datetime | None = None
    last_heard_utc: datetime | None = None
    decode_count: int = 0
    last_snr_db: float | None = None
    last_distance_km: float | None = None


@dataclass
class StationCache:
    """In-memory grid/country cache keyed by base callsign.

    Grid is only stored when a message (or later an external source) supplies one.
    """

    stations: dict[str, CachedStation] = field(default_factory=dict)

    def get(self, callsign: str | None) -> CachedStation | None:
        if not callsign:
            return None
        return self.stations.get(extract_base_call(callsign))

    def remember_grid(self, callsign: str, grid: str, source: str = "message") -> None:
        key = extract_base_call(callsign)
        station = self.stations.setdefault(key, CachedStation(callsign=key))
        if source == "message" or station.grid is None:
            station.grid = grid
            station.grid_source = source

    def update_from_observation(
        self,
        *,
        callsign: str | None,
        grid: str | None,
        grid_source: str,
        country: str | None,
        dxcc: str | None,
        continent: str | None,
        cqz: int | None,
        ituz: int | None,
        when: datetime,
        snr_db: float | None,
        distance_km: float | None,
    ) -> CachedStation | None:
        if not callsign:
            return None
        key = extract_base_call(callsign)
        station = self.stations.setdefault(key, CachedStation(callsign=key))
        if grid and (grid_source == "message" or station.grid is None):
            station.grid = grid
            station.grid_source = grid_source
        if country:
            station.country = country
        if dxcc:
            station.dxcc = dxcc
        if continent:
            station.continent = continent
        if cqz is not None:
            station.cqz = cqz
        if ituz is not None:
            station.ituz = ituz
        station.last_heard_utc = when
        station.first_heard_utc = station.first_heard_utc or when
        station.decode_count += 1
        station.last_snr_db = snr_db
        station.last_distance_km = distance_km
        return station
