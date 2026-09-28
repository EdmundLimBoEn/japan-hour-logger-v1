from __future__ import annotations

from datetime import datetime, timedelta, timezone

import httpx
from fastapi.testclient import TestClient

from radio_logger.api.app import create_app
from radio_logger.models import RawDecode
from radio_logger.propagation.conditions import condition_for_count, trend_for_counts
from radio_logger.propagation.feeds import PropagationFeeds, parse_hamqsl, parse_noaa, propagation_alert
from radio_logger.propagation.solar import solar_clock

HAMQSL_XML = """<?xml version="1.0" encoding="UTF-8"?>
<solar><solardata>
  <updated> 28 Sep 2026 0527 GMT</updated>
  <solarflux>97</solarflux>
  <sunspots>67</sunspots>
  <calculatedconditions>
    <band name="80m-40m" time="day">Good</band>
    <band name="12m-10m" time="day">Poor</band>
    <band name="80m-40m" time="night">Fair</band>
  </calculatedconditions>
</solardata></solar>
"""


def _raw(when: datetime, message: str, dial: int, snr: float = -8) -> RawDecode:
    return RawDecode(
        source="simulator",
        instance_id="SIM",
        decode_time_utc=when,
        snr_db=snr,
        dt=0.1,
        df=500,
        mode="FT8",
        raw_message=message,
        dial_frequency_hz=dial,
    )


def test_equatorial_solar_clock_is_day_at_local_noon_and_night_at_local_midnight():
    noon = solar_clock(1.3521, 103.8198, datetime(2026, 9, 28, 4, 0, tzinfo=timezone.utc))
    midnight = solar_clock(1.3521, 103.8198, datetime(2026, 9, 28, 16, 0, tzinfo=timezone.utc))
    assert noon["state"] == "day"
    assert noon["elevation_deg"] > 50
    assert midnight["state"] == "night"
    assert midnight["elevation_deg"] < 0
    sunrise = datetime.fromisoformat(noon["sunrise_utc"])
    sunset = datetime.fromisoformat(noon["sunset_utc"])
    assert 6 <= (sunrise + timedelta(hours=8)).hour <= 8
    assert 18 <= (sunset + timedelta(hours=8)).hour <= 20


def test_band_words_follow_unique_callsigns_and_ignore_a_single_decode_swing():
    assert condition_for_count(0) == "quiet"
    assert condition_for_count(3) == "poor"
    assert condition_for_count(4) == "fair"
    assert condition_for_count(26) == "excellent"
    assert trend_for_counts(4, 1) == "up"
    assert trend_for_counts(4, 3) == "flat"
    assert trend_for_counts(1, 4) == "down"


def test_noaa_and_hamqsl_parsers_keep_the_newest_valued_sample():
    space = parse_noaa(
        {
            "kp": [
                {"time_tag": "2026-09-27T00:00:00", "Kp": 4, "a_running": 20},
                {"time_tag": "2026-09-28T00:00:00", "Kp": 2.0, "a_running": 7},
            ],
            "flux": [
                {"time_tag": "2026-09-28T22:00:00", "flux": None},
                {"time_tag": "2026-09-28T17:00:00", "flux": 140},
            ],
            "wind": [{"proton_speed": 421, "time_tag": "2026-09-28T05:00:00Z"}],
            "mag": [{"bt": 4, "bz_gsm": -2.8, "time_tag": "2026-09-28T05:00:00Z"}],
            "xray": [{"current_class": "B7.2", "time_tag": "2026-09-28T05:00:00Z"}],
            "scales": {"0": {"R": {"Scale": "3", "Text": "strong"}, "S": {"Scale": "0", "Text": "none"}, "G": {"Scale": "1", "Text": "minor"}}},
        }
    )
    assert space["kp"] == 2.0
    assert space["sfi"] == 140
    assert space["bz_nt"] == -2.8
    assert space["xray_class"] == "B7.2"
    assert propagation_alert(space)["text"].startswith("Radio blackout R3")
    ham = parse_hamqsl(HAMQSL_XML)
    assert ham["sunspots"] == "67"
    assert ham["bands"][0] == {"name": "80m-40m", "time": "day", "rating": "Good"}


def test_propagation_api_uses_cached_feeds_and_local_decodes(ingestor, session_factory, config, runtime):
    now = datetime.now(tz=timezone.utc)
    calls = ["JA1AAA PM95", "JA2BBB PM96", "VK2ABC QF56", "DL1CCC JO62"]
    for index, message in enumerate(calls):
        ingestor.ingest_raw(
            _raw(now - timedelta(minutes=2, seconds=index), f"CQ {message}", 21_074_000, snr=-10 - index),
            skip_dedupe=True,
        )
    ingestor.ingest_raw(_raw(now - timedelta(minutes=20), "CQ JA1OLD PM95", 21_074_000), skip_dedupe=True)

    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if url.endswith("noaa-planetary-k-index.json"):
            return httpx.Response(200, json=[{"time_tag": "2026-09-28T00:00:00", "Kp": 1.5, "a_running": 5}])
        if url.endswith("f107_cm_flux.json"):
            return httpx.Response(200, json=[{"time_tag": "2026-09-28T17:00:00", "flux": 167}])
        if url.endswith("solar-wind-speed.json"):
            return httpx.Response(200, json=[{"proton_speed": 421, "time_tag": "2026-09-28T05:00:00Z"}])
        if url.endswith("solar-wind-mag-field.json"):
            return httpx.Response(200, json=[{"bt": 3, "bz_gsm": -1.2, "time_tag": "2026-09-28T05:00:00Z"}])
        if url.endswith("xray-flares-latest.json"):
            return httpx.Response(200, json=[{"current_class": "B4.1", "time_tag": "2026-09-28T05:00:00Z"}])
        if url.endswith("noaa-scales.json"):
            return httpx.Response(200, json={"0": {"R": {"Scale": "0", "Text": "none"}, "S": {"Scale": "0", "Text": "none"}, "G": {"Scale": "0", "Text": "none"}}})
        if url.endswith("solarxml.php"):
            return httpx.Response(200, text=HAMQSL_XML)
        if "solarsun.php" in url:
            return httpx.Response(200, content=b"GIF87a", headers={"content-type": "image/gif"})
        return httpx.Response(404)

    feeds = PropagationFeeds(httpx.Client(transport=httpx.MockTransport(handler)))
    client = TestClient(create_app(config, session_factory, runtime, feeds))
    body = client.get("/api/propagation").json()
    assert body["space_weather"]["sfi"] == 167
    assert body["space_weather"]["solar_wind_kms"] == 421
    assert body["alert"] is None
    assert body["hamqsl"]["sunspots"] == "67"
    assert body["location"]["label"] == "OJ11"
    band = next(item for item in body["heard"]["bands"] if item["band"] == "15m")
    assert band["unique_calls_15m"] == 4
    assert band["condition"] == "fair"
    assert band["trend"] == "up"
    assert "Japan" in band["regions"]
    image = client.get("/api/propagation/image/sun")
    assert image.status_code == 200
    assert image.content.startswith(b"GIF87a")
    assert client.get("/api/propagation/image/nope").status_code == 404
    again = client.get("/api/propagation").json()
    assert again["space_weather"]["sfi"] == 167
