from __future__ import annotations

import statistics
from collections import defaultdict
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from radio_logger.database.models import Observation

HF_BANDS: tuple[str, ...] = ("80m", "40m", "30m", "20m", "17m", "15m", "12m", "10m")

REGIONS: tuple[tuple[str, str], ...] = (
    ("Japan", "japan"),
    ("Asia", "asia"),
    ("Oceania", "oceania"),
    ("Europe", "europe"),
    ("North America", "north_america"),
    ("South America", "south_america"),
    ("Africa", "africa"),
)


def condition_for_count(unique_calls: int) -> str:
    if unique_calls <= 0:
        return "quiet"
    if unique_calls <= 3:
        return "poor"
    if unique_calls <= 10:
        return "fair"
    if unique_calls <= 25:
        return "good"
    return "excellent"


def trend_for_counts(current: int, previous: int) -> str:
    if current >= previous + 2:
        return "up"
    if previous >= current + 2:
        return "down"
    return "flat"


def confidence_for_count(unique_calls: int) -> float:
    if unique_calls >= 15:
        return 0.9
    if unique_calls >= 5:
        return 0.7
    if unique_calls >= 1:
        return 0.5
    return 0.35


def _region_key(is_japan: bool, continent: str | None) -> str | None:
    if is_japan:
        return "japan"
    code = (continent or "").upper()
    return {
        "AS": "asia",
        "OC": "oceania",
        "EU": "europe",
        "NA": "north_america",
        "SA": "south_america",
        "AF": "africa",
    }.get(code)


def _window_rows(session: Session, since: datetime, until: datetime) -> list[Any]:
    return list(
        session.execute(
            select(
                Observation.band,
                Observation.tx_callsign,
                Observation.snr_db,
                Observation.distance_km,
                Observation.continent,
                Observation.is_japan,
            ).where(
                Observation.timestamp_utc >= since,
                Observation.timestamp_utc < until,
                Observation.band.in_(HF_BANDS),
            )
        ).all()
    )


def _band_stats(rows: list[Any]) -> dict[str, dict[str, Any]]:
    calls: dict[str, set[str]] = defaultdict(set)
    snrs: dict[str, list[float]] = defaultdict(list)
    distances: dict[str, list[float]] = defaultdict(list)
    regions: dict[str, dict[str, set[str]]] = defaultdict(lambda: defaultdict(set))
    for band, callsign, snr, distance, continent, is_japan in rows:
        if band not in HF_BANDS or not callsign:
            continue
        calls[band].add(callsign)
        if snr is not None:
            snrs[band].append(float(snr))
        if distance is not None:
            distances[band].append(float(distance))
        region = _region_key(bool(is_japan), continent)
        if region:
            regions[band][region].add(callsign)
    stats: dict[str, dict[str, Any]] = {}
    for band in HF_BANDS:
        region_counts = {key: len(values) for key, values in regions[band].items()}
        labels = {key: name for name, key in REGIONS}
        ranked = sorted(region_counts, key=lambda key: (-region_counts[key], key))
        stats[band] = {
            "unique": len(calls[band]),
            "median_snr": round(statistics.median(snrs[band]), 1) if snrs[band] else None,
            "max_distance_km": round(max(distances[band])) if distances[band] else None,
            "regions": [labels[key] for key in ranked[:3]],
        }
    return stats


def heard_here(session: Session, now: datetime) -> dict[str, Any]:
    current_since = now - timedelta(minutes=15)
    previous_since = now - timedelta(minutes=30)
    current = _band_stats(_window_rows(session, current_since, now))
    previous = _band_stats(_window_rows(session, previous_since, current_since))
    bands = []
    for band in HF_BANDS:
        unique = current[band]["unique"]
        bands.append(
            {
                "band": band,
                "condition": condition_for_count(unique),
                "trend": trend_for_counts(unique, previous[band]["unique"]),
                "confidence": confidence_for_count(unique),
                "unique_calls_15m": unique,
                "unique_calls_previous_15m": previous[band]["unique"],
                "median_snr": current[band]["median_snr"],
                "max_distance_km": current[band]["max_distance_km"],
                "regions": current[band]["regions"],
            }
        )
    return {
        "window_minutes": 15,
        "basis": "Unique callsigns decoded at this receiver. Quiet means nothing was heard, not that the band is closed.",
        "bands": bands,
    }
