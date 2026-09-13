from __future__ import annotations

import csv
import io
import os
import sqlite3
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path

import pytest
from sqlalchemy import func, select
from sqlalchemy.exc import OperationalError

import radio_logger.database.backup as backups
from radio_logger.database.engine import (
    create_schema,
    database_size_bytes,
    make_engine,
    make_session_factory,
    session_scope,
)
from radio_logger.database.models import Observation, Station
from radio_logger.database.repository import Repository
from radio_logger.export_csv import observations_to_csv
from radio_logger.models import NormalizedDecode


def _decode(message: str = "CQ JA1XYZ PM95") -> NormalizedDecode:
    return NormalizedDecode(
        receiver_id="test-rx",
        timestamp_utc=datetime(2026, 9, 14, tzinfo=timezone.utc),
        timestamp_local=datetime(2026, 9, 14, tzinfo=timezone.utc),
        raw_message=message,
        tx_callsign="JA1XYZ",
        tx_grid="PM95",
        source="udp",
        fingerprint=message,
    )


def _database(tmp_path: Path):
    engine = make_engine(f"sqlite:///{tmp_path / 'radio.db'}")
    create_schema(engine)
    factory = make_session_factory(engine)
    with session_scope(factory) as session:
        Repository(session).upsert_receiver("test-rx", "Test", "OJ11", "Asia/Singapore")
    return engine, factory


def _source_database(path: Path) -> None:
    with closing(sqlite3.connect(path)) as connection:
        connection.execute("CREATE TABLE observations (message TEXT)")
        connection.execute("INSERT INTO observations VALUES ('CQ JA1XYZ PM95')")
        connection.commit()


def test_sqlite_connections_enable_durable_writes_and_bounded_lock_waits(tmp_path):
    engine = make_engine(f"sqlite:///{tmp_path / 'radio.db'}")
    try:
        with engine.connect() as first, engine.connect() as second:
            for connection in (first, second):
                assert connection.exec_driver_sql("PRAGMA journal_mode").scalar() == "wal"
                assert connection.exec_driver_sql("PRAGMA synchronous").scalar() == 2
                assert connection.exec_driver_sql("PRAGMA foreign_keys").scalar() == 1
                assert 0 < connection.exec_driver_sql("PRAGMA busy_timeout").scalar() <= 5000
    finally:
        engine.dispose()


def test_memory_database_is_available_to_worker_threads():
    engine = make_engine("sqlite:///:memory:")
    create_schema(engine)
    try:
        def read_count():
            with engine.connect() as connection:
                return connection.execute(select(func.count()).select_from(Observation)).scalar()

        with ThreadPoolExecutor(max_workers=1) as pool:
            assert pool.submit(read_count).result(timeout=2) == 0
    finally:
        engine.dispose()


def test_database_size_includes_wal_and_shared_memory(tmp_path):
    path = tmp_path / "radio.db"
    engine = make_engine(f"sqlite:///{path}")
    create_schema(engine)
    try:
        files = (path, Path(f"{path}-wal"), Path(f"{path}-shm"))
        expected = sum(file.stat().st_size for file in files)
        assert expected > path.stat().st_size
        assert database_size_bytes(f"sqlite:///{path}") == expected
        assert database_size_bytes(f"sqlite+pysqlite:///{path}?timeout=2") == expected
    finally:
        engine.dispose()


def test_database_size_tolerates_sidecar_removal_during_checkpoint(tmp_path, monkeypatch):
    path = tmp_path / "radio.db"
    path.write_bytes(b"database")
    wal = Path(f"{path}-wal")
    wal.write_bytes(b"wal")
    original_stat = Path.stat

    def disappearing_stat(self, *args, **kwargs):
        if self == wal:
            raise FileNotFoundError(self)
        return original_stat(self, *args, **kwargs)

    monkeypatch.setattr(Path, "stat", disappearing_stat)
    assert database_size_bytes(f"sqlite:///{path}") == len(b"database")


def test_backup_includes_committed_wal_and_excludes_uncommitted_writes(tmp_path):
    source_path = tmp_path / "radio #1.db"
    with closing(sqlite3.connect(source_path)) as source:
        source.execute("PRAGMA journal_mode=WAL")
        source.execute("CREATE TABLE observations (message TEXT)")
        source.execute("INSERT INTO observations VALUES ('committed')")
        source.commit()
        source.execute("INSERT INTO observations VALUES ('not committed')")
        destination = backups.backup_sqlite(f"sqlite:///{source_path}", tmp_path / "backups")
        with closing(sqlite3.connect(destination)) as restored:
            assert restored.execute("SELECT message FROM observations").fetchall() == [("committed",)]
            assert restored.execute("PRAGMA integrity_check").fetchall() == [("ok",)]
            assert restored.execute("PRAGMA journal_mode").fetchone() == ("delete",)
        assert list(destination.parent.iterdir()) == [destination]
        source.rollback()
    destination.unlink()


def test_corrupt_backup_source_leaves_no_completed_or_partial_backup(tmp_path):
    source = tmp_path / "corrupt.db"
    source.write_bytes(b"not a database")
    dest = tmp_path / "backups"
    with pytest.raises(sqlite3.DatabaseError):
        backups.backup_sqlite(f"sqlite:///{source}", dest)
    assert list(dest.iterdir()) == []
    assert source.read_bytes() == b"not a database"


def test_backup_checks_copied_database_before_publishing(tmp_path):
    source = tmp_path / "radio.db"
    with closing(sqlite3.connect(source)) as connection:
        connection.execute("CREATE TABLE observations (message TEXT CHECK(length(message) < 5))")
        connection.execute("PRAGMA ignore_check_constraints=ON")
        connection.execute("INSERT INTO observations VALUES ('invalid record')")
        connection.commit()
    with pytest.raises(sqlite3.DatabaseError, match="integrity check failed"):
        backups.backup_sqlite(f"sqlite:///{source}", tmp_path / "backups")
    assert list((tmp_path / "backups").iterdir()) == []


def test_backup_publication_does_not_overwrite_a_concurrent_destination(tmp_path, monkeypatch):
    source = tmp_path / "radio.db"
    _source_database(source)
    method = "rename" if backups.os.name == "nt" else "link"
    publish = getattr(backups.os, method)
    destination = None

    def publish_after_collision(temporary, dest):
        nonlocal destination
        destination = Path(dest)
        destination.write_bytes(b"existing backup")
        return publish(temporary, dest)

    monkeypatch.setattr(backups.os, method, publish_after_collision)
    with pytest.raises(FileExistsError):
        backups.backup_sqlite(f"sqlite:///{source}", tmp_path / "backups")
    assert destination.read_bytes() == b"existing backup"
    assert list((tmp_path / "backups").iterdir()) == [destination]


def test_backup_write_failure_closes_connections_and_removes_partial(tmp_path, monkeypatch):
    source = tmp_path / "radio.db"
    _source_database(source)
    dest = tmp_path / "backups"
    connections = []
    real_connect = sqlite3.connect

    class FailingConnection(sqlite3.Connection):
        def backup(self, target, **kwargs):
            assert not list(dest.glob("*.db"))
            raise sqlite3.OperationalError("database or disk is full")

    def failing_connect(*args, **kwargs):
        connection = real_connect(*args, **kwargs, factory=FailingConnection)
        connections.append(connection)
        return connection

    monkeypatch.setattr(backups.sqlite3, "connect", failing_connect)
    with pytest.raises(sqlite3.OperationalError, match="full"):
        backups.backup_sqlite(f"sqlite:///{source}", dest)
    assert list(dest.iterdir()) == []
    assert len(connections) == 2
    for connection in connections:
        with pytest.raises(sqlite3.ProgrammingError, match="closed"):
            connection.execute("SELECT 1")


def test_backup_sync_failure_never_publishes_completed_name(tmp_path, monkeypatch):
    source = tmp_path / "radio.db"
    _source_database(source)

    def fail_sync(_descriptor):
        raise OSError("disk write failed")

    monkeypatch.setattr(backups.os, "fsync", fail_sync)
    with pytest.raises(OSError, match="disk write failed"):
        backups.backup_sqlite(f"sqlite:///{source}", tmp_path / "backups")
    assert list((tmp_path / "backups").iterdir()) == []


def test_backup_source_lock_has_a_deadline_and_can_retry(tmp_path):
    source = tmp_path / "radio.db"
    _source_database(source)
    with closing(sqlite3.connect(source)) as lock:
        lock.execute("BEGIN EXCLUSIVE")
        started = time.monotonic()
        with pytest.raises(TimeoutError, match="exceeded"):
            backups.backup_sqlite(f"sqlite:///{source}", tmp_path / "backups", timeout_seconds=0.1)
        assert time.monotonic() - started < 2
        assert list((tmp_path / "backups").iterdir()) == []
        lock.rollback()
    destination = backups.backup_sqlite(f"sqlite:///{source}", tmp_path / "backups")
    with closing(sqlite3.connect(destination)) as restored:
        assert restored.execute("SELECT count(*) FROM observations").fetchone() == (1,)


@pytest.mark.parametrize("timeout", [0, -1, float("inf"), float("nan")])
def test_backup_rejects_unbounded_or_invalid_timeout(tmp_path, timeout):
    with pytest.raises(ValueError, match="finite positive"):
        backups.backup_sqlite(f"sqlite:///{tmp_path / 'radio.db'}", tmp_path, timeout_seconds=timeout)


def test_observation_and_station_roll_back_together(tmp_path):
    engine, factory = _database(tmp_path)
    try:
        with pytest.raises(RuntimeError, match="interrupted"):
            with session_scope(factory) as session:
                Repository(session).insert_observation(_decode())
                raise RuntimeError("interrupted")
        with session_scope(factory) as session:
            assert session.scalar(select(func.count()).select_from(Observation)) == 0
            assert session.scalar(select(func.count()).select_from(Station)) == 0
            Repository(session).insert_observation(_decode())
        with session_scope(factory) as session:
            assert session.scalar(select(func.count()).select_from(Observation)) == 1
            assert session.get(Station, "JA1XYZ").decode_count == 1
    finally:
        engine.dispose()


def test_wal_recovers_committed_data_after_abrupt_process_exit(tmp_path):
    source = tmp_path / "interrupted.db"
    script = """
import os
import sys
from radio_logger.database.engine import make_engine

engine = make_engine('sqlite:///' + sys.argv[1])
with engine.begin() as connection:
    connection.exec_driver_sql('CREATE TABLE observations (message TEXT)')
    connection.exec_driver_sql("INSERT INTO observations VALUES ('committed')")
connection = engine.connect()
transaction = connection.begin()
connection.exec_driver_sql("INSERT INTO observations VALUES ('not committed')")
os._exit(17)
"""
    environment = os.environ.copy()
    environment["PYTHONPATH"] = str(Path(__file__).resolve().parents[1] / "src")
    result = subprocess.run(
        [sys.executable, "-c", script, str(source)],
        env=environment,
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert result.returncode == 17, result.stderr
    with closing(sqlite3.connect(source)) as restored:
        assert restored.execute("PRAGMA integrity_check").fetchone() == ("ok",)
        assert restored.execute("SELECT message FROM observations").fetchall() == [("committed",)]


def test_disk_full_rollback_preserves_existing_data_and_allows_recovery(tmp_path):
    engine, factory = _database(tmp_path)
    try:
        with session_scope(factory) as session:
            Repository(session).insert_observation(_decode())
        with engine.connect() as connection:
            count = connection.exec_driver_sql("PRAGMA page_count").scalar()
            connection.exec_driver_sql(f"PRAGMA max_page_count={count}")
        with pytest.raises(OperationalError, match="full"):
            with session_scope(factory) as session:
                Repository(session).insert_observation(_decode("X" * 100_000))
        with session_scope(factory) as session:
            assert session.scalar(select(func.count()).select_from(Observation)) == 1
            assert session.get(Station, "JA1XYZ").decode_count == 1
        with engine.connect() as connection:
            connection.exec_driver_sql("PRAGMA max_page_count=100000")
            assert connection.exec_driver_sql("PRAGMA integrity_check").scalar() == "ok"
        with session_scope(factory) as session:
            Repository(session).insert_observation(_decode("CQ JA1XYZ PM96"))
        with session_scope(factory) as session:
            assert session.scalar(select(func.count()).select_from(Observation)) == 2
            assert session.get(Station, "JA1XYZ").decode_count == 2
    finally:
        engine.dispose()


@pytest.mark.parametrize("value", ["=1+1", "+SUM(1)", "-2+3", "@SUM(1)", "\tvalue", "\rvalue", "\nvalue", "  =1+1"])
def test_csv_exports_external_text_as_text_without_changing_numeric_data(value):
    row = Observation(raw_message=value, receiver_id=value, snr_db=-12, dt=-0.2)
    exported = next(csv.DictReader(io.StringIO(observations_to_csv([row]))))
    assert exported["raw_message"] == "'" + value
    assert exported["receiver_id"] == "'" + value
    assert exported["snr_db"] == "-12"
    assert exported["dt"] == "-0.2"
    assert row.raw_message == value


def test_repository_export_loads_receiver_timezone_before_session_closes(tmp_path):
    engine, factory = _database(tmp_path)
    try:
        with session_scope(factory) as session:
            Repository(session).insert_observation(_decode())
        session = factory()
        try:
            rows = list(Repository(session).iter_observations(chunk_size=1))
        finally:
            session.close()
        exported = next(csv.DictReader(io.StringIO(observations_to_csv(rows, "UTC"))))
        assert exported["timestamp_local"] == "2026-09-14T08:00:00+08:00"
    finally:
        engine.dispose()
