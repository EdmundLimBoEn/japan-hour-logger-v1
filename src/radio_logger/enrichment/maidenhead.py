from __future__ import annotations

import re

GRID_RE = re.compile(r"^[A-R]{2}[0-9]{2}(?:[A-X]{2})?(?:[0-9]{2})?$", re.IGNORECASE)


def is_grid(token: str | None) -> bool:
    if not token:
        return False
    text = token.strip().upper()
    if text in {"RR73", "RRR73"}:
        return False
    return bool(GRID_RE.match(text)) and len(text) in {4, 6, 8}


def normalize_grid(token: str) -> str:
    return token.strip().upper()


def grid_to_latlon(grid: str) -> tuple[float, float]:
    """Return the center latitude/longitude of a Maidenhead locator.

    Grids are never invented. Callers must pass a real locator from a message,
    station cache, or explicit receiver config.
    """
    g = normalize_grid(grid)
    if not is_grid(g):
        raise ValueError(f"Invalid Maidenhead grid: {grid!r}")

    lon = (ord(g[0]) - ord("A")) * 20.0 - 180.0
    lat = (ord(g[1]) - ord("A")) * 10.0 - 90.0
    lon += int(g[2]) * 2.0
    lat += int(g[3]) * 1.0

    if len(g) >= 6:
        lon += (ord(g[4]) - ord("A")) * (2.0 / 24.0)
        lat += (ord(g[5]) - ord("A")) * (1.0 / 24.0)
        if len(g) >= 8:
            lon += int(g[6]) * (2.0 / 240.0)
            lat += int(g[7]) * (1.0 / 240.0)
            lon += 2.0 / 480.0
            lat += 1.0 / 480.0
        else:
            lon += 2.0 / 48.0
            lat += 1.0 / 48.0
    else:
        lon += 1.0
        lat += 0.5
    return lat, lon
