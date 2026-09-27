from __future__ import annotations

import statistics
from collections import defaultdict
from collections.abc import Iterator
from dataclasses import dataclass, field
from datetime import datetime, time, timedelta, timezone
from typing import Any

from sqlalchemy import Row, case, func, select
from sqlalchemy.orm import Session

from radio_logger.database.models import Observation
from radio_logger.timeutil import as_utc, floor_bucket, to_local, tz_name

JAPAN_RELATIVE_SNR = "median_japan_snr - median_non_japan_snr"
MAX_PADDED_BUCKETS = 10_000


def _median(values: list[float]) -> float | None:
    if not values:
        return None
    return float(statistics.median(values))


def _mean(values: list[float]) -> float | None:
    if not values:
        return None
    return float(statistics.fmean(values))


def _metric_rows(
    session: Session, *, since: datetime | None, until: datetime | None
) -> Iterator[Row[Any]]:
    stmt = select(
        Observation.timestamp_utc,
        Observation.tx_callsign,
        Observation.country,
        Observation.is_japan,
        Observation.snr_db,
        Observation.distance_km,
    )
    if since:
        stmt = stmt.where(Observation.timestamp_utc >= as_utc(since))
    if until:
        stmt = stmt.where(Observation.timestamp_utc < as_utc(until))
    yield from session.execute(stmt.execution_options(yield_per=1000))


@dataclass
class _Metrics:
    total_decodes: int = 0
    japan_decodes: int = 0
    calls: set[str] = field(default_factory=set)
    japan_calls: set[str] = field(default_factory=set)
    countries: set[str] = field(default_factory=set)
    japan_countries: set[str] = field(default_factory=set)
    snrs: list[float] = field(default_factory=list)
    japan_snr: list[float] = field(default_factory=list)
    non_japan_snr: list[float] = field(default_factory=list)
    japan_dist: list[float] = field(default_factory=list)

    def add(self, row: Row[Any]) -> None:
        self.total_decodes += 1
        if row.tx_callsign:
            self.calls.add(row.tx_callsign)
        if row.country:
            self.countries.add(row.country)
        if row.snr_db is not None:
            self.snrs.append(row.snr_db)
            (self.japan_snr if row.is_japan else self.non_japan_snr).append(row.snr_db)
        if row.is_japan:
            self.japan_decodes += 1
            if row.tx_callsign:
                self.japan_calls.add(row.tx_callsign)
            if row.country:
                self.japan_countries.add(row.country)
            if row.distance_km is not None:
                self.japan_dist.append(row.distance_km)

    def result(self, *, full: bool = True) -> dict[str, Any]:
        median_j = _median(self.japan_snr)
        median_nj = _median(self.non_japan_snr)
        relative = None if median_j is None or median_nj is None else round(median_j - median_nj, 2)
        result = {
            "total_decodes": self.total_decodes,
            "unique_callsigns": len(self.calls),
            "unique_countries": len(self.countries),
            "japan_decodes": self.japan_decodes,
            "japan_unique_callsigns": len(self.japan_calls),
            "japan_unique_countries": len(self.japan_countries),
            "japan_decode_share": _share(self.japan_decodes, self.total_decodes),
            "japan_unique_station_share": _share(len(self.japan_calls), len(self.calls)),
            "median_all_snr": _median(self.snrs),
            "median_japan_snr": median_j,
            "mean_japan_snr": _mean(self.japan_snr),
            "min_japan_snr": min(self.japan_snr) if self.japan_snr else None,
            "max_japan_snr": max(self.japan_snr) if self.japan_snr else None,
            "median_non_japan_snr": median_nj,
            "japan_relative_snr": relative,
            "median_japan_distance_km": _median(self.japan_dist),
            "max_japan_distance_km": max(self.japan_dist) if self.japan_dist else None,
            "min_japan_distance_km": min(self.japan_dist) if self.japan_dist else None,
        }
        if full:
            result.update(
                mean_all_snr=_mean(self.snrs),
                min_all_snr=min(self.snrs) if self.snrs else None,
                max_all_snr=max(self.snrs) if self.snrs else None,
                japan_relative_snr_definition=JAPAN_RELATIVE_SNR,
                mean_japan_distance_km=_mean(self.japan_dist),
            )
        return result


def summary(session: Session, *, since: datetime | None = None, until: datetime | None = None) -> dict[str, Any]:
    metrics = _Metrics()
    for row in _metric_rows(session, since=since, until=until):
        metrics.add(row)
    return metrics.result()


def lifetime_totals(session: Session) -> dict[str, Any]:
    callsign = func.nullif(Observation.tx_callsign, "")
    country = func.nullif(Observation.country, "")
    result = session.execute(
        select(
            func.count().label("total_decodes"),
            func.count(func.distinct(callsign)).label("unique_callsigns"),
            func.count(func.distinct(country)).label("unique_countries"),
            func.count(case((Observation.is_japan.is_(True), 1))).label("japan_decodes"),
            func.count(func.distinct(case((Observation.is_japan.is_(True), callsign)))).label(
                "japan_unique_callsigns"
            ),
            func.min(Observation.timestamp_utc).label("first_decode_at"),
            func.max(Observation.timestamp_utc).label("last_decode_at"),
        )
    ).one()
    return {
        "total_decodes": result.total_decodes,
        "unique_callsigns": result.unique_callsigns,
        "unique_countries": result.unique_countries,
        "japan_decodes": result.japan_decodes,
        "japan_unique_callsigns": result.japan_unique_callsigns,
        "first_decode_at": as_utc(result.first_decode_at).isoformat() if result.first_decode_at else None,
        "last_decode_at": as_utc(result.last_decode_at).isoformat() if result.last_decode_at else None,
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
    zone = tz_name(display_timezone)
    since = as_utc(since) if since is not None else None
    until = as_utc(until) if until is not None else None
    grouped: dict[datetime, _Metrics] = defaultdict(_Metrics)
    if since is not None and until is not None:
        if since >= until:
            raise ValueError("since must be before until")
        first_bucket = floor_bucket(since, bucket_minutes)
        interval = timedelta(minutes=bucket_minutes)
        bucket_count, remainder = divmod(until - first_bucket, interval)
        bucket_count += bool(remainder)
        if bucket_count > MAX_PADDED_BUCKETS:
            raise ValueError(f"time range exceeds {MAX_PADDED_BUCKETS:,} buckets; shorten the range or increase bucket")
        for index in range(bucket_count):
            grouped[first_bucket + index * interval] = _Metrics()

    totals = _Metrics()
    local_hours = _HourlyCounts()
    utc_hours = _HourlyCounts()
    for row in _metric_rows(session, since=since, until=until):
        timestamp = as_utc(row.timestamp_utc)
        grouped[floor_bucket(timestamp, bucket_minutes)].add(row)
        totals.add(row)
        local_hours.add(row, timestamp.astimezone(zone).hour)
        utc_hours.add(row, timestamp.hour)

    buckets = []
    peak = None
    for start in sorted(grouped):
        metrics = grouped[start].result(full=False)
        local_start = start.astimezone(zone)
        item = {
            "bucket_start_utc": start.isoformat(),
            "bucket_start_local": local_start.isoformat(),
            "local_hour": local_start.strftime("%H:%M"),
            "utc_hour": start.strftime("%H:%M"),
            **metrics,
        }
        buckets.append(item)
        if item["japan_decodes"] and (
            peak is None or item["japan_decodes"] > peak["japan_decodes"]
        ):
            peak = item

    return {
        "since": since.isoformat() if since is not None else None,
        "until": until.isoformat() if until is not None else None,
        "bucket_minutes": bucket_minutes,
        "display_timezone": display_timezone,
        "japan_relative_snr_definition": JAPAN_RELATIVE_SNR,
        "totals": totals.result(),
        "peak_local": peak,
        "buckets": buckets,
        "time_of_day_local": local_hours.result(),
        "time_of_day_utc": utc_hours.result(),
    }


@dataclass
class _HourlyCounts:
    decodes: list[int] = field(default_factory=lambda: [0] * 24)
    japan_decodes: list[int] = field(default_factory=lambda: [0] * 24)
    callsigns: list[set[str]] = field(default_factory=lambda: [set() for _ in range(24)])
    japan_callsigns: list[set[str]] = field(default_factory=lambda: [set() for _ in range(24)])

    def add(self, row: Row[Any], hour: int) -> None:
        self.decodes[hour] += 1
        if row.tx_callsign:
            self.callsigns[hour].add(row.tx_callsign)
        if row.is_japan:
            self.japan_decodes[hour] += 1
            if row.tx_callsign:
                self.japan_callsigns[hour].add(row.tx_callsign)

    def result(self) -> list[dict[str, Any]]:
        return [
            {
                "hour": f"{hour:02d}:00",
                "decodes": self.decodes[hour],
                "japan_decodes": self.japan_decodes[hour],
                "unique_callsigns": len(self.callsigns[hour]),
                "japan_unique_callsigns": len(self.japan_callsigns[hour]),
            }
            for hour in range(24)
        ]


def _share(part: int, whole: int) -> float | None:
    if whole == 0:
        return None
    return round(part / whole, 4)


def today_bounds(now: datetime, tz: str) -> tuple[datetime, datetime]:
    local = to_local(now, tz)
    start_local = local.replace(hour=0, minute=0, second=0, microsecond=0)
    next_local = datetime.combine(
        start_local.date() + timedelta(days=1), time.min, tzinfo=start_local.tzinfo
    )
    start_utc = start_local.astimezone(timezone.utc)
    return start_utc, next_local.astimezone(timezone.utc)
