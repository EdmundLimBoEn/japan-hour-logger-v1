from __future__ import annotations

from zoneinfo import ZoneInfo

from datetime import datetime, timezone

UTC = timezone.utc
SGT_NAME = "Asia/Singapore"


def tz_name(name: str | None) -> ZoneInfo:
    return ZoneInfo(name or SGT_NAME)


def as_utc(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        return dt.replace(tzinfo=UTC)
    return dt.astimezone(UTC)


def to_local(dt: datetime, tz: str | ZoneInfo = SGT_NAME) -> datetime:
    zone = tz if isinstance(tz, ZoneInfo) else tz_name(str(tz))
    return as_utc(dt).astimezone(zone)


def combine_utc_date_qtime(day_utc: datetime, millis_since_midnight: int) -> datetime:
    """WSJT-X Decode carries QTime (ms since midnight UTC) without a date."""
    base = as_utc(day_utc).replace(hour=0, minute=0, second=0, microsecond=0)
    return base.fromtimestamp(base.timestamp() + millis_since_midnight / 1000.0, tz=UTC)


def floor_bucket(dt: datetime, minutes: int) -> datetime:
    dt = as_utc(dt)
    minute = (dt.minute // minutes) * minutes
    return dt.replace(minute=minute, second=0, microsecond=0)
