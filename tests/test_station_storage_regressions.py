from __future__ import annotations

import csv
import io
from datetime import datetime, timedelta, timezone

from sqlalchemy import create_engine, inspect

from radio_logger.database.engine import create_schema, db_writable, make_engine, make_session_factory
from radio_logger.database.models import Observation, Receiver
from radio_logger.database.repository import Repository
from radio_logger.enrichment.pipeline import Enricher
from radio_logger.enrichment.station_cache import StationCache
from radio_logger.export_csv import iter_observations_csv, observations_to_csv
from radio_logger.models import NormalizedDecode, RawDecode
from tests.conftest import make_test_config


def _decoded(callsign: str, when: datetime, *, grid: str | None = None, snr: float = -10) -> NormalizedDecode:
    return NormalizedDecode(
        receiver_id="test-rx",
        timestamp_utc=when,
        timestamp_local=when,
        raw_message=f"CQ {callsign}" + (f" {grid}" if grid else ""),
        tx_callsign=callsign,
        tx_grid=grid,
        grid_source="message" if grid else "none",
        snr_db=snr,
        source="udp",
        fingerprint=f"{callsign}-{when.isoformat()}",
    )


def _repository(tmp_path):
    engine = make_engine(f"sqlite:///{tmp_path / 'test.db'}")
    create_schema(engine)
    session = make_session_factory(engine)()
    repo = Repository(session)
    repo.upsert_receiver("test-rx", "Test", "OJ11", "Asia/Singapore")
    return engine, session, repo


def test_reused_grid_is_marked_as_cache(tmp_path):
    enricher = Enricher(make_test_config(tmp_path))
    first = RawDecode(
        source="udp",
        decode_time_utc=datetime(2026, 9, 11, 0, 0, tzinfo=timezone.utc),
        raw_message="CQ JA1XYZ PM95",
    )
    reply = RawDecode(
        source="udp",
        decode_time_utc=datetime(2026, 9, 11, 0, 1, tzinfo=timezone.utc),
        raw_message="9V1XX JA1XYZ -10",
    )

    enricher.enrich(first)
    result = enricher.enrich(reply)

    assert result.tx_grid == "PM95"
    assert result.grid_source == "cache"


def test_portable_locations_are_not_reused_between_operating_callsigns():
    cache = StationCache()
    cache.remember_grid("JA1XYZ/P", "PM95")

    assert cache.get("JA1XYZ/P") is not None
    assert cache.get("JA1XYZ/QRP") is None
    assert cache.get("JA1XYZ") is None


def test_station_history_matches_base_call_not_substrings(tmp_path):
    engine, session, repo = _repository(tmp_path)
    try:
        start = datetime(2026, 9, 11, tzinfo=timezone.utc)
        repo.insert_observation(_decoded("JA1XYZ", start))
        repo.insert_observation(_decoded("JA1XYZ/P", start + timedelta(minutes=1)))
        repo.insert_observation(_decoded("JA1XYZ1", start + timedelta(minutes=2)))
        session.commit()

        history = repo.station_history("JA1XYZ", limit=2)

        assert [row.tx_callsign for row in history] == ["JA1XYZ/P", "JA1XYZ"]
    finally:
        session.close()
        engine.dispose()


def test_historical_replay_keeps_latest_station_metadata(tmp_path):
    engine, session, repo = _repository(tmp_path)
    try:
        latest = datetime(2026, 9, 11, 2, tzinfo=timezone.utc)
        earlier = latest - timedelta(hours=1)
        repo.insert_observation(_decoded("JA1XYZ", latest, grid="PM96", snr=-5))
        repo.insert_observation(_decoded("JA1XYZ", earlier, grid="PM95", snr=-20))
        session.commit()

        station = repo.station_get("JA1XYZ")

        assert station is not None
        assert station.first_heard_utc == earlier.replace(tzinfo=None)
        assert station.last_heard_utc == latest.replace(tzinfo=None)
        assert station.last_grid == "PM96"
        assert station.last_snr_db == -5
        assert station.decode_count == 2
    finally:
        session.close()
        engine.dispose()


def test_station_cache_historical_replay_keeps_latest_metadata():
    cache = StationCache()
    latest = datetime(2026, 9, 11, 2, tzinfo=timezone.utc)
    earlier = (latest - timedelta(hours=1)).replace(tzinfo=None)
    cache.update_from_observation(
        callsign="JA1XYZ",
        grid="PM96",
        grid_source="message",
        country="Japan",
        dxcc="JA",
        continent="AS",
        cqz=25,
        ituz=45,
        when=latest,
        snr_db=-5,
        distance_km=5000,
    )
    cache.update_from_observation(
        callsign="JA1XYZ",
        grid="PM95",
        grid_source="message",
        country="Old",
        dxcc="OLD",
        continent="EU",
        cqz=1,
        ituz=1,
        when=earlier,
        snr_db=-20,
        distance_km=10,
    )

    station = cache.get("JA1XYZ")
    assert station is not None
    assert station.first_heard_utc == earlier.replace(tzinfo=timezone.utc)
    assert station.last_heard_utc == latest
    assert station.grid == "PM96"
    assert station.last_snr_db == -5
    assert station.country == "Japan"


def test_enricher_historical_replay_keeps_latest_cached_grid(tmp_path):
    enricher = Enricher(make_test_config(tmp_path))
    latest = RawDecode(
        source="udp",
        decode_time_utc=datetime(2026, 9, 11, 2, tzinfo=timezone.utc),
        raw_message="CQ JA1XYZ PM96",
    )
    earlier = RawDecode(
        source="udp",
        decode_time_utc=datetime(2026, 9, 11, 1, tzinfo=timezone.utc),
        raw_message="CQ JA1XYZ PM95",
    )

    enricher.enrich(latest)
    enricher.enrich(earlier)

    station = enricher.stations.get("JA1XYZ")
    assert station is not None
    assert station.grid == "PM96"
    assert station.last_heard_utc == latest.decode_time_utc


def test_repository_normalizes_offset_query_bounds_to_utc(tmp_path):
    engine, session, repo = _repository(tmp_path)
    try:
        observed = datetime(2026, 9, 11, 1, 30, tzinfo=timezone.utc)
        repo.insert_observation(_decoded("JA1XYZ", observed))
        session.commit()
        plus_eight = timezone(timedelta(hours=8))
        since = datetime(2026, 9, 11, 9, tzinfo=plus_eight)
        until = datetime(2026, 9, 11, 10, tzinfo=plus_eight)

        rows, total = repo.list_observations(since=since, until=until)

        assert total == 1
        assert [row.tx_callsign for row in rows] == ["JA1XYZ"]
        assert repo.count_since(since, until=until) == 1
    finally:
        session.close()
        engine.dispose()


def test_iter_observations_is_uncapped(tmp_path):
    engine, session, repo = _repository(tmp_path)
    try:
        start = datetime(2026, 9, 11, tzinfo=timezone.utc)
        for index in range(3):
            repo.insert_observation(_decoded(f"JA1XY{index}", start + timedelta(minutes=index)))
        session.commit()

        limited, total = repo.list_observations(limit=2)
        all_rows = list(repo.iter_observations(chunk_size=2))

        assert len(limited) == 2
        assert total == 3
        assert len(all_rows) == 3
    finally:
        session.close()
        engine.dispose()


def test_db_writable_rejects_read_only_sqlite_database(tmp_path):
    path = tmp_path / "readonly.db"
    writable_engine = make_engine(f"sqlite:///{path}")
    create_schema(writable_engine)
    writable_engine.dispose()
    readonly_engine = create_engine(f"sqlite:///file:{path}?mode=ro&uri=true")
    try:
        assert db_writable(readonly_engine) is False
    finally:
        readonly_engine.dispose()


def test_db_writable_does_not_change_sqlite_schema(tmp_path):
    engine = make_engine(f"sqlite:///{tmp_path / 'writable.db'}")
    create_schema(engine)
    before = inspect(engine).get_table_names()

    assert db_writable(engine) is True
    assert db_writable(engine) is True
    assert inspect(engine).get_table_names() == before

    engine.dispose()


def test_csv_marks_naive_sqlite_utc_timestamp_with_offset():
    row = Observation(
        id=1,
        receiver_id="test-rx",
        timestamp_utc=datetime(2026, 9, 11, 1, 0),
        timestamp_local=datetime(2026, 9, 11, 9, 0),
        raw_message="CQ JA1XYZ PM95",
        source="udp",
        fingerprint="one",
    )

    data = next(csv.DictReader(io.StringIO(observations_to_csv([row]))))

    assert data["timestamp_utc"] == "2026-09-11T01:00:00+00:00"


def test_csv_iterator_streams_rows_and_reconstructs_local_timestamp():
    rows = [
        Observation(
            id=index,
            receiver_id="test-rx",
            timestamp_utc=datetime(2026, 9, 11, index, 0),
            timestamp_local=datetime(2000, 1, 1),
            raw_message=f"CQ JA1XY{index}",
            source="udp",
            fingerprint=str(index),
        )
        for index in (1, 2)
    ]

    chunks = list(iter_observations_csv(rows, timezone_name="Asia/Singapore"))
    data = list(csv.DictReader(io.StringIO("".join(chunks))))

    assert len(chunks) == 3
    assert [item["timestamp_local"] for item in data] == [
        "2026-09-11T09:00:00+08:00",
        "2026-09-11T10:00:00+08:00",
    ]


def test_csv_uses_each_observations_receiver_timezone(tmp_path):
    engine, session, repo = _repository(tmp_path)
    try:
        now = datetime(2026, 9, 11, 12, tzinfo=timezone.utc)
        session.add(
            Receiver(
                id="new-york-rx",
                name="New York",
                locator="FN30",
                timezone="America/New_York",
                created_at=now,
                updated_at=now,
            )
        )
        singapore = repo.insert_observation(_decoded("JA1AAA", now))
        new_york_decode = _decoded("JA1BBB", now + timedelta(minutes=1))
        new_york_decode.receiver_id = "new-york-rx"
        new_york = repo.insert_observation(new_york_decode)
        session.commit()

        data = list(
            csv.DictReader(
                io.StringIO(
                    observations_to_csv(
                        [singapore, new_york], timezone_name="UTC"
                    )
                )
            )
        )

        assert [row["timestamp_local"] for row in data] == [
            "2026-09-11T20:00:00+08:00",
            "2026-09-11T08:01:00-04:00",
        ]
    finally:
        session.close()
        engine.dispose()
