from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy import func, select

from radio_logger.database.models import Observation
from radio_logger.models import RawDecode
from radio_logger.wsjtx.all_txt import parse_all_txt
from radio_logger.wsjtx.protocol import encode_decode, encode_status


def test_db_preserves_repeated_callsigns(ingestor, session_factory):
    base = datetime(2026, 9, 11, 0, 0, tzinfo=timezone.utc)
    for i in range(20):
        raw = RawDecode(
            source="udp",
            instance_id="WSJT-X",
            decode_time_utc=base + timedelta(seconds=15 * i),
            snr_db=-12 - (i % 3),
            dt=0.2,
            df=1234 + i,
            mode="FT8",
            raw_message="CQ JA1XYZ PM95",
            dial_frequency_hz=14_074_000,
        )
        assert ingestor.ingest_raw(raw) is not None
    session = session_factory()
    try:
        count = session.scalar(select(func.count()).select_from(Observation)) or 0
        ja = session.scalar(
            select(func.count()).select_from(Observation).where(Observation.tx_callsign == "JA1XYZ")
        )
        assert count == 20
        assert ja == 20
    finally:
        session.close()


def test_network_duplicate_window_only(ingestor, session_factory):
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
    assert ingestor.ingest_raw(raw) is not None
    assert ingestor.ingest_raw(raw.model_copy()) is None
    session = session_factory()
    try:
        count = session.scalar(select(func.count()).select_from(Observation))
        assert count == 1
    finally:
        session.close()


def test_udp_status_plus_decode_path(ingestor, session_factory):
    ingestor.handle_datagram(encode_status(dial_frequency_hz=14_074_000, de_grid="OJ11"))
    packet = encode_decode("CQ JA1XYZ PM95", snr=-12, dt=0.2, df=1234, time_ms=15_000)
    row = ingestor.handle_datagram(packet)
    assert row is not None
    assert row.tx_callsign == "JA1XYZ"
    assert row.country == "Japan"
    assert row.tx_grid == "PM95"
    assert row.grid_source == "message"
    assert row.is_japan is True
    assert row.band == "20m"
    assert row.dial_frequency_hz == 14_074_000
    assert row.distance_km is not None
    assert 5000 < row.distance_km < 5600


def test_all_txt_same_normalized_shape(ingestor, session_factory):
    text = "260911_000015    14.074 Rx FT8    -12  0.2 1234 CQ JA1XYZ PM95\n"
    decoded = parse_all_txt(text)
    assert len(decoded) == 1
    row = ingestor.ingest_raw(decoded[0])
    assert row is not None
    assert row.source == "all_txt"
    assert row.tx_callsign == "JA1XYZ"
    assert row.country == "Japan"
