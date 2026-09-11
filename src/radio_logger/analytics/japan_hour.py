from __future__ import annotations

import statistics
from collections import defaultdict
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from radio_logger.database.models import Observation
from radio_logger.timeutil import as_utc, floor_bucket, to_local, tz_name

JAPAN_RELATIVE_SNR = "median_japan_snr - median_non_japan_snr"


def _median(values: list[float]) -> float | None:
    if not values:
        return None
    return float(statistics.median(values))


def _mean(values: list[float]) -> float | None:
    if not values:
        return None
    return float(statistics.fmean(values))


def summary(session: Session, *, since: datetime | None = None, until: datetime | None = None) -> dict[str, Any]:
    stmt = select(Observation)
    if since:
        stmt = stmt.where(Observation.timestamp_utc >= as_utc(since))
    if until:
        stmt = stmt.where(Observation.timestamp_utc < as_utc(until))
    rows = list(session.scalars(stmt))
    snrs = [r.snr_db for r in rows if r.snr_db is not None]
    japan = [r for r in rows if r.is_japan]
    japan_snr = [r.snr_db for r in japan if r.snr_db is not None]
    non_japan_snr = [r.snr_db for r in rows if not r.is_japan and r.snr_db is not None]
    japan_dist = [r.distance_km for r in japan if r.distance_km is not None]
    calls = {r.tx_callsign for r in rows if r.tx_callsign}
    japan_calls = {r.tx_callsign for r in japan if r.tx_callsign}
    countries = {r.country for r in rows if r.country}
    japan_countries = {r.country for r in japan if r.country}
    median_j = _median(japan_snr)
    median_nj = _median(non_japan_snr)
    relative = None if median_j is None or median_nj is None else round(median_j - median_nj, 2)
    return {
        "total_decodes": len(rows),
        "unique_callsigns": len(calls),
        "unique_countries": len(countries),
        "japan_decodes": len(japan),
        "japan_unique_callsigns": len(japan_calls),
        "japan_unique_countries": len(japan_countries),
        "japan_decode_share": _share(len(japan), len(rows)),
        "japan_unique_station_share": _share(len(japan_calls), len(calls)),
        "median_all_snr": _median(snrs),
        "mean_all_snr": _mean(snrs),
        "min_all_snr": min(snrs) if snrs else None,
        "max_all_snr": max(snrs) if snrs else None,
        "median_japan_snr": median_j,
        "mean_japan_snr": _mean(japan_snr),
        "min_japan_snr": min(japan_snr) if japan_snr else None,
        "max_japan_snr": max(japan_snr) if japan_snr else None,
        "median_non_japan_snr": median_nj,
        "japan_relative_snr": relative,
        "japan_relative_snr_definition": JAPAN_RELATIVE_SNR,
        "median_japan_distance_km": _median(japan_dist),
        "max_japan_distance_km": max(japan_dist) if japan_dist else None,
        "min_japan_distance_km": min(japan_dist) if japan_dist else None,
        "mean_japan_distance_km": _mean(japan_dist),
    }


def country_mix(session: Session, *, since: datetime | None = None, until: datetime | None = None, limit: int = 20) -> list[dict[str, Any]]:
    stmt = (
        select(
            Observation.country,
            func.count().label("decodes"),
            func.count(func.distinct(Observation.tx_callsign)).label("stations"),
        )
        .where(Observation.country.is_not(None))
        .group_by(Observation.country)
        .order_by(func.count().desc())
        .limit(limit)
    )
    if since:
        stmt = stmt.where(Observation.timestamp_utc >= as_utc(since))
    if until:
        stmt = stmt.where(Observation.timestamp_utc < as_utc(until))
    return [
        {"country": country, "decodes": int(decodes), "stations": int(stations)}
        for country, decodes, stations in session.execute(stmt)
    ]


def distance_histogram(session: Session, *, since: datetime | None = None, until: datetime | None = None) -> list[dict[str, Any]]:
    bins = [(0, 500), (500, 1500), (1500, 3000), (3000, 5000), (5000, 8000), (8000, 20000)]
    stmt = select(Observation.distance_km).where(Observation.distance_km.is_not(None))
    if since:
        stmt = stmt.where(Observation.timestamp_utc >= as_utc(since))
    if until:
        stmt = stmt.where(Observation.timestamp_utc < as_utc(until))
    distances = [row[0] for row in session.execute(stmt)]
    out = []
    for lo, hi in bins:
        count = sum(1 for d in distances if lo <= d < hi)
        out.append({"bucket_km": f"{lo}-{hi}", "count": count})
    return out


def snr_histogram(session: Session, *, since: datetime | None = None, until: datetime | None = None) -> dict[str, Any]:
    stmt = select(Observation.snr_db, Observation.is_japan).where(Observation.snr_db.is_not(None))
    if since:
        stmt = stmt.where(Observation.timestamp_utc >= as_utc(since))
    if until:
        stmt = stmt.where(Observation.timestamp_utc < as_utc(until))
    all_snr = []
    japan_snr = []
    for snr, is_japan in session.execute(stmt):
        all_snr.append(snr)
        if is_japan:
            japan_snr.append(snr)
    bins = list(range(-24, 22, 4))
    def hist(values: list[float]) -> list[dict[str, Any]]:
        out = []
        for i, lo in enumerate(bins):
            hi = bins[i + 1] if i + 1 < len(bins) else 40
            out.append({"lo": lo, "hi": hi, "count": sum(1 for v in values if lo <= v < hi)})
        return out
    return {"all": hist(all_snr), "japan": hist(japan_snr)}


def japan_hour_buckets(
    session: Session,
    *,
    since: datetime | None = None,
    until: datetime | None = None,
    bucket_minutes: int = 15,
    display_timezone: str = "Asia/Singapore",
) -> dict[str, Any]:
    if bucket_minutes not in {5, 15, 30, 60}:
        raise ValueError("bucket_minutes must be 5, 15, 30, or 60")
    stmt = select(Observation)
    if since:
        stmt = stmt.where(Observation.timestamp_utc >= as_utc(since))
    if until:
        stmt = stmt.where(Observation.timestamp_utc < as_utc(until))
    rows = list(session.scalars(stmt.order_by(Observation.timestamp_utc.asc())))
    grouped: dict[datetime, list[Observation]] = defaultdict(list)
    for row in rows:
        grouped[floor_bucket(row.timestamp_utc, bucket_minutes)].append(row)

    buckets = []
    peak = None
    for start in sorted(grouped):
        group = grouped[start]
        metrics = _bucket_metrics(group)
        local_start = to_local(start, display_timezone)
        item = {
            "bucket_start_utc": start.isoformat(),
            "bucket_start_local": local_start.isoformat(),
            "local_hour": local_start.strftime("%H:%M"),
            "utc_hour": start.strftime("%H:%M"),
            **metrics,
        }
        buckets.append(item)
        if peak is None or item["japan_decodes"] > peak["japan_decodes"]:
            peak = item

    totals = summary(session, since=since, until=until)
    return {
        "bucket_minutes": bucket_minutes,
        "display_timezone": display_timezone,
        "japan_relative_snr_definition": JAPAN_RELATIVE_SNR,
        "totals": totals,
        "peak_local": peak,
        "buckets": buckets,
        "time_of_day_local": _time_of_day(rows, display_timezone),
        "time_of_day_utc": _time_of_day(rows, "UTC"),
    }


def _bucket_metrics(rows: list[Observation]) -> dict[str, Any]:
    japan = [r for r in rows if r.is_japan]
    calls = {r.tx_callsign for r in rows if r.tx_callsign}
    japan_calls = {r.tx_callsign for r in japan if r.tx_callsign}
    countries = {r.country for r in rows if r.country}
    japan_countries = {r.country for r in japan if r.country}
    japan_snr = [r.snr_db for r in japan if r.snr_db is not None]
    all_snr = [r.snr_db for r in rows if r.snr_db is not None]
    non_japan_snr = [r.snr_db for r in rows if not r.is_japan and r.snr_db is not None]
    japan_dist = [r.distance_km for r in japan if r.distance_km is not None]
    median_j = _median(japan_snr)
    median_nj = _median(non_japan_snr)
    relative = None if median_j is None or median_nj is None else round(median_j - median_nj, 2)
    return {
        "total_decodes": len(rows),
        "unique_callsigns": len(calls),
        "unique_countries": len(countries),
        "japan_decodes": len(japan),
        "japan_unique_callsigns": len(japan_calls),
        "japan_unique_countries": len(japan_countries),
        "japan_decode_share": _share(len(japan), len(rows)),
        "japan_unique_station_share": _share(len(japan_calls), len(calls)),
        "median_japan_snr": median_j,
        "mean_japan_snr": _mean(japan_snr),
        "max_japan_snr": max(japan_snr) if japan_snr else None,
        "min_japan_snr": min(japan_snr) if japan_snr else None,
        "median_all_snr": _median(all_snr),
        "median_non_japan_snr": median_nj,
        "japan_relative_snr": relative,
        "median_japan_distance_km": _median(japan_dist),
        "max_japan_distance_km": max(japan_dist) if japan_dist else None,
        "min_japan_distance_km": min(japan_dist) if japan_dist else None,
    }


def _time_of_day(rows: list[Observation], tz: str) -> list[dict[str, Any]]:
    hours = defaultdict(lambda: {"decodes": 0, "japan_decodes": 0, "callsigns": set(), "japan_callsigns": set()})
    zone = tz_name(tz)
    for row in rows:
        hour = as_utc(row.timestamp_utc).astimezone(zone).strftime("%H:00")
        bucket = hours[hour]
        bucket["decodes"] += 1
        if row.tx_callsign:
            bucket["callsigns"].add(row.tx_callsign)
        if row.is_japan:
            bucket["japan_decodes"] += 1
            if row.tx_callsign:
                bucket["japan_callsigns"].add(row.tx_callsign)
    out = []
    for hour in [f"{h:02d}:00" for h in range(24)]:
        bucket = hours[hour]
        out.append(
            {
                "hour": hour,
                "decodes": bucket["decodes"],
                "japan_decodes": bucket["japan_decodes"],
                "unique_callsigns": len(bucket["callsigns"]),
                "japan_unique_callsigns": len(bucket["japan_callsigns"]),
            }
        )
    return out


def _share(part: int, whole: int) -> float | None:
    if whole == 0:
        return None
    return round(part / whole, 4)


def today_bounds(now: datetime, tz: str) -> tuple[datetime, datetime]:
    from datetime import timezone

    local = to_local(now, tz)
    start_local = local.replace(hour=0, minute=0, second=0, microsecond=0)
    start_utc = start_local.astimezone(timezone.utc)
    return start_utc, start_utc + timedelta(days=1)
