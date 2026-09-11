from __future__ import annotations

from datetime import datetime, timedelta, timezone

from fastapi.testclient import TestClient

from radio_logger.analytics.japan_hour import japan_hour_buckets
from radio_logger.api.app import create_app
from radio_logger.models import RawDecode


def _raw(when: datetime, message: str, snr: float = -12) -> RawDecode:
    return RawDecode(
        source="simulator",
        instance_id="SIM",
        decode_time_utc=when,
        snr_db=snr,
        dt=0.2,
        df=1000,
        mode="FT8",
        raw_message=message,
        dial_frequency_hz=14_074_000,
    )


def test_japan_hour_relative_and_absolute(ingestor, session_factory):
    start = datetime(2026, 9, 11, 0, 0, tzinfo=timezone.utc)
    for i in range(10):
        ingestor.ingest_raw(_raw(start + timedelta(minutes=i), "CQ JA1XYZ PM95", snr=-10), skip_dedupe=True)
        ingestor.ingest_raw(_raw(start + timedelta(minutes=i, seconds=1), "CQ VK2ABC QF56", snr=-16), skip_dedupe=True)
    session = session_factory()
    try:
        report = japan_hour_buckets(session, since=start, until=start + timedelta(hours=1), bucket_minutes=15)
    finally:
        session.close()
    totals = report["totals"]
    assert totals["total_decodes"] == 20
    assert totals["japan_decodes"] == 10
    assert totals["japan_decode_share"] == 0.5
    assert totals["japan_relative_snr"] == 6.0
    assert report["japan_relative_snr_definition"] == "median_japan_snr - median_non_japan_snr"
    assert report["buckets"]
    assert report["time_of_day_local"][8]["hour"] == "08:00"
    assert report["time_of_day_local"][8]["japan_decodes"] == 10


def test_api_and_dashboard(ingestor, session_factory, config, runtime):
    ingestor.ingest_raw(_raw(datetime(2026, 9, 11, 0, 0, tzinfo=timezone.utc), "CQ JA1XYZ PM95"), skip_dedupe=True)
    app = create_app(config, session_factory, runtime)
    client = TestClient(app)
    home = client.get("/")
    assert home.status_code == 200
    assert "JAPAN HOUR LOGGER" in home.text
    status = client.get("/api/status")
    assert status.status_code == 200
    body = status.json()
    assert body["db_writable"] is True
    latest = client.get("/api/observations/latest")
    assert latest.json()["items"][0]["tx_callsign"] == "JA1XYZ"
    summary = client.get("/api/stats/summary")
    assert summary.json()["japan_decodes"] >= 1
    jh = client.get("/api/stats/japan-hour?bucket=15")
    assert jh.status_code == 200
    csv = client.get("/api/export/csv")
    assert csv.status_code == 200
    assert "tx_callsign" in csv.text
    stations = client.get("/api/stations")
    assert stations.json()["total"] >= 1
    hist = client.get("/api/stations/JA1XYZ/history")
    assert hist.status_code == 200
