from __future__ import annotations

from datetime import datetime, timezone

from radio_logger.enrichment.pipeline import Enricher
from radio_logger.models import RawDecode
from tests.conftest import make_test_config


def test_example_cq_ja1xyz_pm95_enriches(enricher: Enricher):
    raw = RawDecode(
        source="udp",
        instance_id="WSJT-X",
        decode_time_utc=datetime(2026, 9, 11, 0, 0, 15, tzinfo=timezone.utc),
        snr_db=-12,
        dt=0.2,
        df=1234,
        mode="FT8",
        raw_message="CQ JA1XYZ PM95",
        dial_frequency_hz=14_074_000,
    )
    obs = enricher.enrich(raw)
    assert obs.tx_callsign == "JA1XYZ"
    assert obs.tx_grid == "PM95"
    assert obs.grid_source == "message"
    assert obs.country == "Japan"
    assert obs.dxcc == "JA"
    assert obs.continent == "AS"
    assert obs.is_cq is True
    assert obs.message_type == "cq"
    assert obs.is_japan is True
    assert obs.band == "20m"
    assert obs.timestamp_local.tzinfo is not None
    assert obs.timestamp_local.hour == 8  # SGT = UTC+8
    assert obs.distance_km is not None
    assert obs.bearing_deg is not None
    assert obs.snr_db == -12


def test_grid_comes_from_cache_not_fabricated(enricher: Enricher):
    first = RawDecode(
        source="udp",
        decode_time_utc=datetime(2026, 9, 11, 0, 0, 15, tzinfo=timezone.utc),
        snr_db=-12,
        dt=0.2,
        df=100,
        raw_message="CQ JA1XYZ PM95",
        dial_frequency_hz=14_074_000,
    )
    second = RawDecode(
        source="udp",
        decode_time_utc=datetime(2026, 9, 11, 0, 0, 30, tzinfo=timezone.utc),
        snr_db=-10,
        dt=0.1,
        df=200,
        raw_message="9V1XX JA1XYZ R-08",
        dial_frequency_hz=14_074_000,
    )
    enricher.enrich(first)
    obs = enricher.enrich(second)
    assert obs.tx_grid == "PM95"
    assert obs.grid_source == "cache"


def test_no_grid_without_message_or_cache():
    cfg = make_test_config()
    cfg.receiver.locator = "TODO"
    enricher = Enricher(cfg)
    raw = RawDecode(
        source="udp",
        decode_time_utc=datetime(2026, 9, 11, 0, 0, 15, tzinfo=timezone.utc),
        snr_db=-12,
        dt=0.2,
        df=100,
        raw_message="9V1XX JA1XYZ RRR",
        dial_frequency_hz=14_074_000,
    )
    obs = enricher.enrich(raw)
    assert obs.tx_grid is None
    assert obs.grid_source == "none"
    assert obs.distance_km is None
    assert obs.country == "Japan"


def test_unknown_message_still_enriches_payload(enricher: Enricher):
    raw = RawDecode(
        source="udp",
        decode_time_utc=datetime(2026, 9, 11, 0, 0, 15, tzinfo=timezone.utc),
        snr_db=-22,
        dt=0.7,
        df=450,
        raw_message="!!! garbled ???",
        dial_frequency_hz=14_074_000,
    )
    obs = enricher.enrich(raw)
    assert obs.message_type == "unknown"
    assert obs.raw_message == "!!! garbled ???"
    assert obs.tx_callsign is None
