from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy.orm import Session

from radio_logger.config import AppConfig
from radio_logger.enrichment.maidenhead import grid_to_latlon, is_grid
from radio_logger.propagation.conditions import heard_here
from radio_logger.propagation.feeds import PropagationFeeds, propagation_alert
from radio_logger.propagation.solar import solar_clock

SINGAPORE = (1.3521, 103.8198)


def receiver_point(config: AppConfig) -> tuple[float, float, str]:
    locator = (config.receiver.locator or "").upper()
    if is_grid(locator):
        latitude, longitude = grid_to_latlon(locator)
        return latitude, longitude, locator
    return SINGAPORE[0], SINGAPORE[1], "Singapore"


def build_snapshot(
    session: Session,
    config: AppConfig,
    feeds: PropagationFeeds,
    now: datetime,
) -> dict[str, Any]:
    latitude, longitude, label = receiver_point(config)
    space = feeds.space_weather(now)
    hamqsl = feeds.hamqsl(now)
    values = space.get("values") or {}
    return {
        "generated_at": now.isoformat(),
        "location": {"label": label, "latitude": round(latitude, 4), "longitude": round(longitude, 4)},
        "solar": solar_clock(latitude, longitude, now),
        "space_weather": {
            **values,
            "stale": space.get("stale", False),
            "error": space.get("error"),
            "fetched_at": space["fetched_at"].isoformat() if space.get("fetched_at") else None,
        },
        "alert": propagation_alert(values),
        "heard": heard_here(session, now),
        "hamqsl": {
            **(hamqsl.get("values") or {}),
            "stale": hamqsl.get("stale", False),
            "error": hamqsl.get("error"),
            "fetched_at": hamqsl["fetched_at"].isoformat() if hamqsl.get("fetched_at") else None,
            "images": [
                {"id": "bands", "title": "HF band summary", "href": "https://www.hamqsl.com/solar101pic.php"},
            ],
        },
    }
