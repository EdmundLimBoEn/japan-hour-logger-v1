from __future__ import annotations

import csv
import io
from collections.abc import Iterable, Iterator

from radio_logger.database.models import Observation
from radio_logger.timeutil import as_utc, to_local

CSV_FIELDS = [
    "id",
    "receiver_id",
    "timestamp_utc",
    "timestamp_local",
    "dial_frequency_hz",
    "audio_frequency_hz",
    "signal_frequency_hz",
    "band",
    "mode",
    "snr_db",
    "dt",
    "df",
    "raw_message",
    "message_type",
    "is_cq",
    "tx_callsign",
    "rx_callsign",
    "tx_grid",
    "grid_source",
    "country",
    "dxcc",
    "continent",
    "cqz",
    "ituz",
    "distance_km",
    "bearing_deg",
    "low_confidence",
    "off_air",
    "source",
    "is_japan",
]


def _csv_row(row: Observation, timezone_name: str | None) -> dict[str, object]:
    timestamp_utc = as_utc(row.timestamp_utc) if row.timestamp_utc else None
    row_timezone = row.receiver.timezone if row.receiver else timezone_name
    timestamp_local = (
        to_local(timestamp_utc, row_timezone)
        if timestamp_utc and row_timezone
        else row.timestamp_local
    )
    return {
        "id": row.id,
        "receiver_id": row.receiver_id,
        "timestamp_utc": timestamp_utc.isoformat() if timestamp_utc else "",
        "timestamp_local": timestamp_local.isoformat() if timestamp_local else "",
        "dial_frequency_hz": row.dial_frequency_hz,
        "audio_frequency_hz": row.audio_frequency_hz,
        "signal_frequency_hz": row.signal_frequency_hz,
        "band": row.band,
        "mode": row.mode,
        "snr_db": row.snr_db,
        "dt": row.dt,
        "df": row.df,
        "raw_message": row.raw_message,
        "message_type": row.message_type,
        "is_cq": row.is_cq,
        "tx_callsign": row.tx_callsign,
        "rx_callsign": row.rx_callsign,
        "tx_grid": row.tx_grid,
        "grid_source": row.grid_source,
        "country": row.country,
        "dxcc": row.dxcc,
        "continent": row.continent,
        "cqz": row.cqz,
        "ituz": row.ituz,
        "distance_km": row.distance_km,
        "bearing_deg": row.bearing_deg,
        "low_confidence": row.low_confidence,
        "off_air": row.off_air,
        "source": row.source,
        "is_japan": row.is_japan,
    }


def iter_observations_csv(
    rows: Iterable[Observation], timezone_name: str | None = None
) -> Iterator[str]:
    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=CSV_FIELDS)
    writer.writeheader()
    yield buf.getvalue()
    for row in rows:
        buf.seek(0)
        buf.truncate(0)
        writer.writerow(_csv_row(row, timezone_name))
        yield buf.getvalue()


def observations_to_csv(rows: Iterable[Observation], timezone_name: str | None = None) -> str:
    return "".join(iter_observations_csv(rows, timezone_name))
