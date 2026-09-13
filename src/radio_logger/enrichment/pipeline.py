from __future__ import annotations

from datetime import datetime

from radio_logger.bands import band_from_hz, signal_frequency_hz
from radio_logger.config import AppConfig
from radio_logger.enrichment.bearing import bearing_deg
from radio_logger.enrichment.distance import haversine_km
from radio_logger.enrichment.dxcc import DxccLookup, default_lookup
from radio_logger.enrichment.maidenhead import grid_to_latlon, is_grid, normalize_grid
from radio_logger.enrichment.station_cache import StationCache
from radio_logger.ft8.message import parse_ft8_message
from radio_logger.models import GridSource, NormalizedDecode, RawDecode
from radio_logger.timeutil import as_utc, to_local


class Enricher:
    def __init__(
        self,
        config: AppConfig,
        *,
        dxcc: DxccLookup | None = None,
        stations: StationCache | None = None,
    ):
        self.config = config
        self.dxcc = dxcc or default_lookup()
        self.stations = stations or StationCache()
        self._receiver_ll = _receiver_latlon(config)

    def enrich(self, raw: RawDecode) -> NormalizedDecode:
        timestamp_utc = as_utc(raw.decode_time_utc)
        timestamp_local = to_local(timestamp_utc, self.config.receiver.timezone)
        parsed = parse_ft8_message(raw.raw_message)

        grid, grid_source = self._resolve_grid(parsed.tx_callsign, parsed.tx_grid)
        dxcc_match = None
        try:
            dxcc_match = self.dxcc.lookup(parsed.tx_callsign)
        except Exception:
            dxcc_match = None

        distance = None
        bearing = None
        if grid and self._receiver_ll:
            try:
                tx_ll = grid_to_latlon(grid)
                distance = round(haversine_km(*self._receiver_ll, *tx_ll), 1)
                bearing = round(bearing_deg(*self._receiver_ll, *tx_ll), 1)
            except Exception:
                distance = None
                bearing = None

        audio = raw.df
        dial = raw.dial_frequency_hz
        signal = signal_frequency_hz(dial, audio)
        band = band_from_hz(signal if signal is not None else dial)
        country = dxcc_match.entity_name if dxcc_match else None
        japan_names = {n.lower() for n in self.config.japan.dxcc_entities}
        is_japan = bool(country and country.lower() in japan_names)

        normalized = NormalizedDecode(
            receiver_id=self.config.receiver.id,
            timestamp_utc=timestamp_utc,
            timestamp_local=timestamp_local,
            dial_frequency_hz=dial,
            audio_frequency_hz=audio,
            signal_frequency_hz=signal,
            band=band,
            mode=raw.mode or "FT8",
            snr_db=raw.snr_db,
            dt=raw.dt,
            df=raw.df,
            raw_message=raw.raw_message,
            message_type=parsed.message_type,
            is_cq=parsed.is_cq,
            tx_callsign=parsed.tx_callsign,
            rx_callsign=parsed.rx_callsign,
            tx_grid=grid,
            grid_source=grid_source,
            country=country,
            dxcc=dxcc_match.primary_prefix if dxcc_match else None,
            continent=dxcc_match.continent if dxcc_match else None,
            cqz=dxcc_match.cqz if dxcc_match else None,
            ituz=dxcc_match.ituz if dxcc_match else None,
            distance_km=distance,
            bearing_deg=bearing,
            low_confidence=raw.low_confidence,
            off_air=raw.off_air,
            source=raw.source,
            instance_id=raw.instance_id,
            fingerprint=raw.fingerprint(),
            raw_payload=raw.raw_payload,
            is_japan=is_japan,
        )
        self.stations.update_from_observation(
            callsign=normalized.tx_callsign,
            grid=normalized.tx_grid,
            grid_source=normalized.grid_source,
            country=normalized.country,
            dxcc=normalized.dxcc,
            continent=normalized.continent,
            cqz=normalized.cqz,
            ituz=normalized.ituz,
            when=timestamp_utc,
            snr_db=normalized.snr_db,
            distance_km=normalized.distance_km,
        )
        return normalized

    def _resolve_grid(self, callsign: str | None, message_grid: str | None) -> tuple[str | None, GridSource]:
        if message_grid and is_grid(message_grid):
            grid = normalize_grid(message_grid)
            return grid, "message"
        cached = self.stations.get(callsign) if callsign else None
        if cached and cached.grid:
            return cached.grid, "cache"
        return None, "none"


def _receiver_latlon(config: AppConfig) -> tuple[float, float] | None:
    grid = config.receiver_grid
    if not grid:
        return None
    try:
        return grid_to_latlon(grid)
    except ValueError:
        return None


def enrich_or_passthrough(enricher: Enricher, raw: RawDecode) -> NormalizedDecode:
    """Enrichment failures still produce a storeable decode."""
    try:
        return enricher.enrich(raw)
    except Exception as exc:
        ts = as_utc(raw.decode_time_utc)
        return NormalizedDecode(
            receiver_id=enricher.config.receiver.id,
            timestamp_utc=ts,
            timestamp_local=to_local(ts, enricher.config.receiver.timezone),
            dial_frequency_hz=raw.dial_frequency_hz,
            audio_frequency_hz=raw.df,
            signal_frequency_hz=signal_frequency_hz(raw.dial_frequency_hz, raw.df),
            band=band_from_hz(raw.dial_frequency_hz),
            mode=raw.mode or "FT8",
            snr_db=raw.snr_db,
            dt=raw.dt,
            df=raw.df,
            raw_message=raw.raw_message,
            message_type="unknown",
            source=raw.source,
            instance_id=raw.instance_id,
            fingerprint=raw.fingerprint(),
            raw_payload={**raw.raw_payload, "enrichment_error": str(exc)},
            low_confidence=raw.low_confidence,
            off_air=raw.off_air,
        )
