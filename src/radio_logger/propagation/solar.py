from __future__ import annotations

import math
from datetime import datetime, timedelta, timezone


def _julian_day(when: datetime) -> float:
    moment = when.astimezone(timezone.utc)
    year, month = moment.year, moment.month
    day = moment.day + (moment.hour + moment.minute / 60 + moment.second / 3600) / 24
    if month <= 2:
        year -= 1
        month += 12
    century = math.floor(year / 100)
    gregorian = 2 - century + math.floor(century / 4)
    return (
        math.floor(365.25 * (year + 4716))
        + math.floor(30.6001 * (month + 1))
        + day
        + gregorian
        - 1524.5
    )


def _solar_terms(when: datetime) -> tuple[float, float, float]:
    """Return declination degrees, equation of time minutes, and fractional century."""
    century = (_julian_day(when) - 2451545.0) / 36525.0
    geom_mean_long = (280.46646 + century * (36000.76983 + century * 0.0003032)) % 360
    geom_mean_anom = 357.52911 + century * (35999.05029 - 0.0001537 * century)
    anomaly = math.radians(geom_mean_anom)
    eccentricity = 0.016708634 - century * (0.000042037 + 0.0000001267 * century)
    equation_center = (
        math.sin(anomaly) * (1.914602 - century * (0.004817 + 0.000014 * century))
        + math.sin(2 * anomaly) * (0.019993 - 0.000101 * century)
        + math.sin(3 * anomaly) * 0.000289
    )
    sun_true_long = geom_mean_long + equation_center
    omega = 125.04 - 1934.136 * century
    apparent_long = sun_true_long - 0.00569 - 0.00478 * math.sin(math.radians(omega))
    mean_obliquity = (
        23
        + (26 + (21.448 - century * (46.815 + century * (0.00059 - century * 0.001813))) / 60) / 60
    )
    obliquity = mean_obliquity + 0.00256 * math.cos(math.radians(omega))
    declination = math.degrees(
        math.asin(math.sin(math.radians(obliquity)) * math.sin(math.radians(apparent_long)))
    )
    obliquity_y = math.tan(math.radians(obliquity / 2)) ** 2
    mean_long = math.radians(geom_mean_long)
    equation_of_time = 4 * math.degrees(
        obliquity_y * math.sin(2 * mean_long)
        - 2 * eccentricity * math.sin(anomaly)
        + 4 * eccentricity * obliquity_y * math.sin(anomaly) * math.cos(2 * mean_long)
        - 0.5 * obliquity_y * obliquity_y * math.sin(4 * mean_long)
        - 1.25 * eccentricity * eccentricity * math.sin(2 * anomaly)
    )
    return declination, equation_of_time, century


def elevation_deg(latitude: float, longitude: float, when: datetime) -> float:
    declination, equation_of_time, _century = _solar_terms(when)
    moment = when.astimezone(timezone.utc)
    minutes = moment.hour * 60 + moment.minute + moment.second / 60
    true_solar = (minutes + equation_of_time + 4 * longitude) % 1440
    hour_angle = true_solar / 4 - 180
    lat = math.radians(latitude)
    decl = math.radians(declination)
    hour = math.radians(hour_angle)
    sine = math.sin(lat) * math.sin(decl) + math.cos(lat) * math.cos(decl) * math.cos(hour)
    return math.degrees(math.asin(max(-1.0, min(1.0, sine))))


def _event_minutes(latitude: float, longitude: float, declination: float, equation_of_time: float) -> tuple[float, float] | None:
    cosine = -math.tan(math.radians(latitude)) * math.tan(math.radians(declination))
    if cosine <= -1 or cosine >= 1:
        return None
    hour_angle = math.degrees(math.acos(cosine))
    noon = 720 - 4 * longitude - equation_of_time
    return noon - hour_angle * 4, noon + hour_angle * 4


def solar_clock(latitude: float, longitude: float, when: datetime) -> dict[str, object]:
    moment = when.astimezone(timezone.utc)
    declination, equation_of_time, _century = _solar_terms(moment)
    elevation = elevation_deg(latitude, longitude, moment)
    if elevation > 0:
        state = "day"
    elif elevation > -6:
        state = "twilight"
    else:
        state = "night"
    events = _event_minutes(latitude, longitude, declination, equation_of_time)
    midnight = moment.replace(hour=0, minute=0, second=0, microsecond=0)
    sunrise = sunset = None
    minutes_to_change = None
    next_event = None
    if events is not None:
        sunrise = midnight + timedelta(minutes=events[0])
        sunset = midnight + timedelta(minutes=events[1])
        target = sunset if state == "day" else sunrise
        if state != "day" and moment > sunrise:
            target = midnight + timedelta(days=1, minutes=events[0])
        minutes_to_change = max(0, int((target - moment).total_seconds() // 60))
        next_event = "sunset" if state == "day" else "sunrise"
    return {
        "state": state,
        "elevation_deg": round(elevation, 1),
        "sunrise_utc": sunrise.isoformat() if sunrise else None,
        "sunset_utc": sunset.isoformat() if sunset else None,
        "next_event": next_event,
        "minutes_to_next": minutes_to_change,
    }
