from __future__ import annotations

import threading
from datetime import datetime, timedelta, timezone
from typing import Any
from xml.etree import ElementTree

import httpx

NOAA_URLS = {
    "kp": "https://services.swpc.noaa.gov/products/noaa-planetary-k-index.json",
    "flux": "https://services.swpc.noaa.gov/json/f107_cm_flux.json",
    "wind": "https://services.swpc.noaa.gov/products/summary/solar-wind-speed.json",
    "mag": "https://services.swpc.noaa.gov/products/summary/solar-wind-mag-field.json",
    "xray": "https://services.swpc.noaa.gov/json/goes/primary/xray-flares-latest.json",
    "scales": "https://services.swpc.noaa.gov/products/noaa-scales.json",
}

HAMQSL_XML = "https://www.hamqsl.com/solarxml.php"

HAMQSL_IMAGES = {
    "sun": "https://www.hamqsl.com/solarsun.php",
    "muf": "https://www.hamqsl.com/solarmuf.php",
    "bands": "https://www.hamqsl.com/solar101pic.php",
}

NOAA_TTL = timedelta(minutes=5)
HAMQSL_TTL = timedelta(hours=1)
IMAGE_TTL = timedelta(minutes=15)
USER_AGENT = "japan-hour-logger"


def _latest(rows: list[dict[str, Any]], field: str) -> dict[str, Any] | None:
    best: dict[str, Any] | None = None
    best_tag = ""
    for row in rows:
        if not isinstance(row, dict) or row.get(field) is None:
            continue
        tag = str(row.get("time_tag") or "")
        if tag >= best_tag:
            best, best_tag = row, tag
    return best


def _scale(block: dict[str, Any] | None) -> dict[str, Any] | None:
    if not isinstance(block, dict):
        return None
    raw = block.get("Scale")
    if raw is None or raw == "":
        return None
    try:
        level = int(raw)
    except (TypeError, ValueError):
        return None
    return {"level": level, "text": block.get("Text") or ""}


def parse_noaa(payloads: dict[str, Any]) -> dict[str, Any]:
    kp_row = _latest(payloads.get("kp") or [], "Kp")
    flux_row = _latest(payloads.get("flux") or [], "flux")
    wind_rows = payloads.get("wind") or []
    mag_rows = payloads.get("mag") or []
    xray_rows = payloads.get("xray") or []
    wind = wind_rows[-1] if isinstance(wind_rows, list) and wind_rows else {}
    mag = mag_rows[-1] if isinstance(mag_rows, list) and mag_rows else {}
    xray = xray_rows[0] if isinstance(xray_rows, list) and xray_rows else {}
    scales = payloads.get("scales") if isinstance(payloads.get("scales"), dict) else {}
    current = scales.get("0") if isinstance(scales.get("0"), dict) else {}
    radio = _scale(current.get("R") if isinstance(current.get("R"), dict) else None)
    proton = _scale(current.get("S") if isinstance(current.get("S"), dict) else None)
    geomagnetic = _scale(current.get("G") if isinstance(current.get("G"), dict) else None)
    return {
        "sfi": flux_row.get("flux") if flux_row else None,
        "sfi_time": flux_row.get("time_tag") if flux_row else None,
        "kp": kp_row.get("Kp") if kp_row else None,
        "ap": kp_row.get("a_running") if kp_row else None,
        "kp_time": kp_row.get("time_tag") if kp_row else None,
        "solar_wind_kms": wind.get("proton_speed") if isinstance(wind, dict) else None,
        "bz_nt": mag.get("bz_gsm") if isinstance(mag, dict) else None,
        "bt_nt": mag.get("bt") if isinstance(mag, dict) else None,
        "wind_time": wind.get("time_tag") if isinstance(wind, dict) else None,
        "xray_class": xray.get("current_class") if isinstance(xray, dict) else None,
        "xray_time": xray.get("time_tag") if isinstance(xray, dict) else None,
        "radio_blackout": radio,
        "proton": proton,
        "geomagnetic": geomagnetic,
        "source": "NOAA SWPC",
    }


def parse_hamqsl(xml_text: str) -> dict[str, Any]:
    root = ElementTree.fromstring(xml_text)
    solar = root.find("solardata")
    if solar is None:
        raise ValueError("HamQSL XML has no solardata")

    def text(tag: str) -> str | None:
        node = solar.find(tag)
        if node is None or node.text is None:
            return None
        value = node.text.strip()
        return value or None

    bands = []
    for band in solar.findall("calculatedconditions/band"):
        bands.append(
            {
                "name": band.attrib.get("name"),
                "time": band.attrib.get("time"),
                "rating": (band.text or "").strip(),
            }
        )
    return {
        "updated": text("updated"),
        "solar_flux": text("solarflux"),
        "a_index": text("aindex"),
        "k_index": text("kindex"),
        "xray": text("xray"),
        "sunspots": text("sunspots"),
        "solar_wind": text("solarwind"),
        "magnetic_field": text("magneticfield"),
        "bands": bands,
        "source": "HamQSL",
    }


def propagation_alert(space: dict[str, Any] | None) -> dict[str, Any] | None:
    if not space:
        return None
    parts = []
    radio = space.get("radio_blackout") or {}
    geomagnetic = space.get("geomagnetic") or {}
    proton = space.get("proton") or {}
    if radio.get("level", 0) >= 1:
        parts.append(f"Radio blackout R{radio['level']} {radio.get('text') or ''}".strip())
    if geomagnetic.get("level", 0) >= 4:
        parts.append(f"Geomagnetic storm G{geomagnetic['level']} {geomagnetic.get('text') or ''}".strip())
    if proton.get("level", 0) >= 2:
        parts.append(f"Solar radiation S{proton['level']} {proton.get('text') or ''}".strip())
    if not parts:
        return None
    sunlit = ""
    if radio.get("level", 0) >= 1:
        sunlit = " On the sunlit side of Earth, HF can fade until the flare subsides."
    return {"text": ". ".join(parts) + "." + sunlit}


class PropagationFeeds:
    def __init__(self, client: httpx.Client | None = None) -> None:
        self._client = client
        self._lock = threading.Lock()
        self._space: dict[str, Any] | None = None
        self._space_at: datetime | None = None
        self._space_error: str | None = None
        self._hamqsl: dict[str, Any] | None = None
        self._hamqsl_at: datetime | None = None
        self._hamqsl_error: str | None = None
        self._images: dict[str, tuple[datetime, bytes, str]] = {}

    def client(self) -> httpx.Client:
        if self._client is None:
            self._client = httpx.Client(
                timeout=8.0,
                follow_redirects=True,
                headers={"User-Agent": USER_AGENT},
            )
        return self._client

    def _get_json(self, url: str) -> Any:
        response = self.client().get(url)
        response.raise_for_status()
        return response.json()

    def space_weather(self, now: datetime | None = None) -> dict[str, Any]:
        moment = now or datetime.now(tz=timezone.utc)
        with self._lock:
            fresh = self._space is not None and self._space_at is not None and moment - self._space_at < NOAA_TTL
            cached = self._space
            cached_error = self._space_error
        if fresh and cached is not None:
            return {"values": cached, "error": None, "stale": False, "fetched_at": self._space_at}
        payloads: dict[str, Any] = {}
        errors: list[str] = []
        for name, url in NOAA_URLS.items():
            try:
                payloads[name] = self._get_json(url)
            except (httpx.HTTPError, ValueError) as exc:
                errors.append(f"{name}: {exc.__class__.__name__}")
        parsed = parse_noaa(payloads) if payloads else None
        with self._lock:
            if parsed and any(parsed.get(key) is not None for key in ("sfi", "kp", "solar_wind_kms", "xray_class")):
                self._space = parsed
                self._space_at = moment
                self._space_error = "; ".join(errors) or None
                return {
                    "values": parsed,
                    "error": self._space_error,
                    "stale": False,
                    "fetched_at": moment,
                }
            return {
                "values": cached,
                "error": "; ".join(errors) or cached_error or "NOAA unavailable",
                "stale": cached is not None,
                "fetched_at": self._space_at,
            }

    def hamqsl(self, now: datetime | None = None) -> dict[str, Any]:
        moment = now or datetime.now(tz=timezone.utc)
        with self._lock:
            fresh = self._hamqsl is not None and self._hamqsl_at is not None and moment - self._hamqsl_at < HAMQSL_TTL
            cached = self._hamqsl
        if fresh and cached is not None:
            return {"values": cached, "error": None, "stale": False, "fetched_at": self._hamqsl_at}
        try:
            response = self.client().get(HAMQSL_XML)
            response.raise_for_status()
            parsed = parse_hamqsl(response.text)
        except (httpx.HTTPError, ValueError, ElementTree.ParseError) as exc:
            with self._lock:
                return {
                    "values": cached,
                    "error": exc.__class__.__name__,
                    "stale": cached is not None,
                    "fetched_at": self._hamqsl_at,
                }
        with self._lock:
            self._hamqsl = parsed
            self._hamqsl_at = moment
            self._hamqsl_error = None
            return {"values": parsed, "error": None, "stale": False, "fetched_at": moment}

    def image(self, name: str, now: datetime | None = None) -> tuple[bytes, str] | None:
        if name not in HAMQSL_IMAGES:
            raise KeyError(name)
        moment = now or datetime.now(tz=timezone.utc)
        with self._lock:
            cached = self._images.get(name)
            if cached and moment - cached[0] < IMAGE_TTL:
                return cached[1], cached[2]
        try:
            response = self.client().get(HAMQSL_IMAGES[name])
            response.raise_for_status()
            media = response.headers.get("content-type", "image/gif").split(";")[0].strip()
            body = response.content
            if not media.startswith("image/") or not body or len(body) > 2_000_000:
                raise ValueError("unexpected image")
        except (httpx.HTTPError, ValueError):
            with self._lock:
                old = self._images.get(name)
                return (old[1], old[2]) if old else None
        with self._lock:
            self._images[name] = (moment, body, media)
        return body, media
