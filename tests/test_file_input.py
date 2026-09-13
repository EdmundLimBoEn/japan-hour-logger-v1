from __future__ import annotations

import json
import shutil
from datetime import datetime, timezone
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError
from sqlalchemy import func, select

from radio_logger.api.app import create_app
from radio_logger.config import AppConfig, InputConfig, load_config
from radio_logger.database.models import InputCursor, Observation, Station
from radio_logger.database.repository import Repository
from radio_logger.ingest import Ingestor
from radio_logger.models import RuntimeState
from radio_logger.wsjtx.file_input import AllTxtFollower, MAX_BATCH_BYTES


def line(message: str = "JQ1GNQ BG7HFE OL67", second: int = 15) -> bytes:
    return f"260913_1130{second:02d}    21.074 Rx FT8    -15  0.0 1047 {message}\n".encode()


def rows(factory):
    with factory() as session:
        return list(session.scalars(select(Observation).order_by(Observation.id)))


def test_file_input_requires_a_path_and_keeps_udp_default(tmp_path):
    assert AppConfig().input.source == "udp"
    with pytest.raises(ValidationError, match="input.path"):
        InputConfig(source="all_txt")
    path = tmp_path / "receiver.yaml"
    path.write_text(f"input:\n  source: all_txt\n  path: {tmp_path / 'ALL.TXT'}\n")
    assert load_config(path).input.path == str(tmp_path / "ALL.TXT")


def test_alembic_upgrade_adopts_cursor_table_created_during_startup(config, tmp_path, monkeypatch):
    from alembic import command
    from alembic.config import Config
    from sqlalchemy import text
    from radio_logger.service import init_database
    from radio_logger.wsjtx.all_txt import parse_all_txt

    root = Path(__file__).resolve().parents[1]
    migration_config = Config(str(root / "alembic.ini"))
    migration_config.set_main_option("script_location", str(root / "alembic"))
    monkeypatch.setenv("RADIO_LOGGER_DATABASE__URL", config.database.url)
    monkeypatch.chdir(tmp_path)
    command.upgrade(migration_config, "0001_initial")
    factory = init_database(config)
    ingestor = Ingestor(config, factory, RuntimeState(started_at=datetime.now(timezone.utc)))
    ingestor.ingest_raw(parse_all_txt(line().decode())[0])
    command.upgrade(migration_config, "head")
    command.upgrade(migration_config, "head")
    with factory() as session:
        assert session.execute(text("SELECT version_num FROM alembic_version")).scalar_one() == "0002_input_cursors"
        assert session.scalar(select(func.count()).select_from(Observation)) == 1
        assert session.scalar(select(func.count()).select_from(InputCursor)) == 0


def test_alembic_rejects_an_incompatible_existing_cursor_table(config, tmp_path, monkeypatch):
    from alembic import command
    from alembic.config import Config
    from sqlalchemy import create_engine, text

    root = Path(__file__).resolve().parents[1]
    migration_config = Config(str(root / "alembic.ini"))
    migration_config.set_main_option("script_location", str(root / "alembic"))
    monkeypatch.setenv("RADIO_LOGGER_DATABASE__URL", config.database.url)
    monkeypatch.chdir(tmp_path)
    command.upgrade(migration_config, "0001_initial")
    engine = create_engine(config.database.url)
    with engine.begin() as connection:
        connection.execute(text("CREATE TABLE input_cursors (unexpected INTEGER)"))
    engine.dispose()
    with pytest.raises(RuntimeError, match="expected schema"):
        command.upgrade(migration_config, "head")


def test_live_line_uses_existing_enrichment_and_durable_provenance(ingestor, tmp_path, session_factory):
    path = tmp_path / "ALL.TXT"
    path.write_bytes(line())
    with_follower = AllTxtFollower(path, ingestor)
    try:
        assert with_follower.poll_once() == 1
        assert with_follower.poll_once() == 0
    finally:
        with_follower.close()
    row = rows(session_factory)[0]
    assert row.timestamp_utc == datetime(2026, 9, 13, 11, 30, 15)
    assert row.dial_frequency_hz == 21_074_000
    assert row.signal_frequency_hz == 21_075_047
    assert row.band == "15m"
    assert row.tx_callsign == "BG7HFE"
    assert row.tx_grid == "OL67"
    assert row.source == "all_txt"
    payload = json.loads(row.raw_payload)
    assert payload["all_txt_path"] == str(path)
    assert payload["all_txt_offset"] == 0
    assert len(payload["all_txt_generation"]) == 32


def test_restart_appends_once_and_preserves_identical_source_lines(ingestor, tmp_path, session_factory):
    path = tmp_path / "ALL.TXT"
    path.write_bytes(line() * 2)
    first = AllTxtFollower(path, ingestor)
    assert first.poll_once() == 2
    first.close()
    second = AllTxtFollower(path, ingestor)
    try:
        assert second.poll_once() == 0
        with path.open("ab") as handle:
            handle.write(line(second=30))
        assert second.poll_once() == 1
        assert second.poll_once() == 0
    finally:
        second.close()
    assert len(rows(session_factory)) == 3


def test_partial_line_waits_for_newline_across_restart(ingestor, tmp_path, session_factory):
    path = tmp_path / "ALL.TXT"
    path.write_bytes(line() + line(second=30)[:35])
    follower = AllTxtFollower(path, ingestor)
    assert follower.poll_once() == 1
    assert follower.poll_once() == 0
    follower.close()
    with path.open("ab") as handle:
        handle.write(line(second=30)[35:-1])
    follower = AllTxtFollower(path, ingestor)
    try:
        assert follower.poll_once() == 0
        with path.open("ab") as handle:
            handle.write(b"\n")
        assert follower.poll_once() == 1
    finally:
        follower.close()
    assert len(rows(session_factory)) == 2


def test_invalid_complete_lines_advance_cursor(ingestor, tmp_path, session_factory):
    path = tmp_path / "ALL.TXT"
    data = b"# header\nmalformed\n" + line().replace(b" Rx ", b" Tx ") + line()
    path.write_bytes(data)
    follower = AllTxtFollower(path, ingestor)
    try:
        assert follower.poll_once() == 1
        assert follower.position.offset == len(data)
        assert follower.poll_once() == 0
    finally:
        follower.close()
    assert len(rows(session_factory)) == 1


def test_malformed_numeric_fields_do_not_poison_following_decodes(
    ingestor, tmp_path, session_factory
):
    path = tmp_path / "ALL.TXT"
    path.write_bytes(
        line().replace(b"21.074", b"9" * 400)
        + line().replace(b"21.074", b"9" * 25)
        + line().replace(b"1047", b"9" * 25)
        + line().replace(b"1047", str(2**63 - 1).encode())
        + line().replace(b"-15", b"9" * 400)
        + line().replace(b"0.0", b"9" * 400)
        + line()
    )
    follower = AllTxtFollower(path, ingestor)
    try:
        assert follower.poll_once() == 1
        assert follower.position.offset == path.stat().st_size
        assert follower.poll_once() == 0
    finally:
        follower.close()
    assert len(rows(session_factory)) == 1


def test_batch_failure_rolls_back_observations_station_counts_and_cursor(
    ingestor, tmp_path, session_factory, monkeypatch
):
    path = tmp_path / "ALL.TXT"
    path.write_bytes(line() + line(second=30))
    original = Repository.insert_observation
    calls = 0

    def fail_second(self, decoded, session_id=None):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise RuntimeError("injected second insert failure")
        return original(self, decoded, session_id=session_id)

    monkeypatch.setattr(Repository, "insert_observation", fail_second)
    follower = AllTxtFollower(path, ingestor)
    try:
        with pytest.raises(RuntimeError, match="injected"):
            follower.poll_once()
        with session_factory() as session:
            assert session.scalar(select(func.count()).select_from(Observation)) == 0
            assert session.scalar(select(func.count()).select_from(InputCursor)) == 0
            assert session.scalar(select(func.count()).select_from(Station)) == 0
        assert not ingestor.enricher.stations.stations
        assert ingestor.runtime.decode_count_session == 0
        assert follower.poll_once() == 2
        assert follower.poll_once() == 0
        assert ingestor.runtime.last_error is None
    finally:
        follower.close()
    assert len(rows(session_factory)) == 2


def test_failed_commit_retries_the_same_bytes(ingestor, tmp_path, session_factory, monkeypatch):
    from sqlalchemy.orm import Session

    path = tmp_path / "ALL.TXT"
    path.write_bytes(line())
    original = Session.commit
    failed = False

    def fail_once(self):
        nonlocal failed
        if not failed:
            failed = True
            raise RuntimeError("injected commit failure")
        return original(self)

    monkeypatch.setattr(Session, "commit", fail_once)
    follower = AllTxtFollower(path, ingestor)
    try:
        with pytest.raises(RuntimeError, match="injected"):
            follower.poll_once()
        assert ingestor.file_position(str(path)) is None
        assert not rows(session_factory)
        assert follower.poll_once() == 1
    finally:
        follower.close()
    assert len(rows(session_factory)) == 1


@pytest.mark.parametrize("restart", [False, True])
def test_rename_rotation_drains_old_inode_then_current(
    ingestor, tmp_path, session_factory, restart
):
    path = tmp_path / "ALL.TXT"
    path.write_bytes(line())
    follower = AllTxtFollower(path, ingestor)
    assert follower.poll_once() == 1
    with path.open("ab") as handle:
        handle.write(line(second=30))
    path.rename(tmp_path / "ALL.TXT.1")
    path.write_bytes(line(second=45))
    if restart:
        follower.close()
        follower = AllTxtFollower(path, ingestor)
    try:
        assert follower.poll_once() == 1
        assert follower.poll_once() == 1
        assert follower.poll_once() == 0
    finally:
        follower.close()
    assert [row.timestamp_utc.second for row in rows(session_factory)] == [15, 30, 45]


@pytest.mark.parametrize("regrow", [False, True])
def test_copytruncate_detects_shrink_and_regrowth(ingestor, tmp_path, session_factory, regrow):
    path = tmp_path / "ALL.TXT"
    path.write_bytes(line() * 2)
    follower = AllTxtFollower(path, ingestor)
    assert follower.poll_once() == 2
    original_generation = follower.position.generation
    count = 3 if regrow else 1
    path.write_bytes(line(second=30) * count)
    try:
        assert follower.poll_once() == count
        assert follower.position.generation != original_generation
        assert follower.poll_once() == 0
    finally:
        follower.close()
    assert len(rows(session_factory)) == 2 + count


def test_copytruncate_recovers_unread_retained_suffix(ingestor, tmp_path, session_factory):
    path = tmp_path / "ALL.TXT"
    path.write_bytes(line())
    follower = AllTxtFollower(path, ingestor)
    assert follower.poll_once() == 1
    with path.open("ab") as handle:
        handle.write(line(second=30))
    shutil.copy2(path, tmp_path / "ALL.TXT.1")
    path.write_bytes(line(second=45))
    try:
        assert follower.poll_once() == 1
        assert follower.poll_once() == 1
    finally:
        follower.close()
    assert [row.timestamp_utc.second for row in rows(session_factory)] == [15, 30, 45]


def test_partial_rotated_line_is_not_joined_to_new_file(ingestor, tmp_path, session_factory):
    path = tmp_path / "ALL.TXT"
    path.write_bytes(line() + b"260913_1130")
    follower = AllTxtFollower(path, ingestor)
    assert follower.poll_once() == 1
    path.rename(tmp_path / "ALL.TXT.1")
    path.write_bytes(line(second=30))
    try:
        assert follower.poll_once() == 1
        assert follower.state.recovery_warning
    finally:
        follower.close()
    assert len(rows(session_factory)) == 2


def test_stale_follower_cannot_commit_the_same_batch(ingestor, tmp_path, session_factory):
    path = tmp_path / "ALL.TXT"
    path.write_bytes(line())
    first = AllTxtFollower(path, ingestor)
    second = AllTxtFollower(path, ingestor)
    try:
        assert first.poll_once() == 1
        with pytest.raises(RuntimeError):
            second.poll_once()
        assert second.poll_once() == 0
    finally:
        first.close()
        second.close()
    assert len(rows(session_factory)) == 1


def test_missing_file_recovers_and_oversized_line_is_bounded(ingestor, tmp_path):
    path = tmp_path / "ALL.TXT"
    follower = AllTxtFollower(path, ingestor)
    try:
        with pytest.raises(FileNotFoundError):
            follower.poll_once()
        assert follower.state.readable is False
        path.write_bytes(b"x" * MAX_BATCH_BYTES)
        with pytest.raises(ValueError, match="exceeds"):
            follower.poll_once()
        assert follower.position is None
        path.write_bytes(line())
        assert follower.poll_once() == 1
        assert follower.state.readable is True
        assert follower.state.error is None
    finally:
        follower.close()


def test_oversized_incomplete_rotated_file_does_not_block_replacement(
    ingestor, tmp_path, session_factory
):
    path = tmp_path / "ALL.TXT"
    path.write_bytes(line() + b"x" * MAX_BATCH_BYTES)
    follower = AllTxtFollower(path, ingestor)
    try:
        assert follower.poll_once() == 1
        with pytest.raises(ValueError, match="exceeds"):
            follower.poll_once()
        path.rename(tmp_path / "ALL.TXT.1")
        path.write_bytes(line(second=30))
        assert follower.poll_once() == 1
        assert follower.poll_once() == 0
        assert follower.state.error is None
    finally:
        follower.close()
    assert len(rows(session_factory)) == 2


def test_file_health_uses_readability_and_restart_retains_latest_decode(
    config, ingestor, tmp_path, session_factory
):
    path = tmp_path / "ALL.TXT"
    path.write_bytes(line())
    config.input = InputConfig(source="all_txt", path=str(path))
    restarted = Ingestor(config, session_factory, RuntimeState(started_at=datetime.now(timezone.utc)))
    follower = AllTxtFollower(path, restarted)
    client = TestClient(create_app(config, session_factory, restarted.runtime))
    assert client.get("/health").status_code == 503
    try:
        follower.poll_once()
        restarted.runtime.last_decode_at = None
        restarted.runtime.receiver_status.dial_frequency_hz = None
        response = client.get("/health")
    finally:
        follower.close()
    assert response.status_code == 200
    status = response.json()
    assert status["input_source"] == "all_txt"
    assert status["input_readable"] is True
    assert status["input_offset"] == status["input_size"] == len(line())
    assert status["input_backlog_bytes"] == 0
    assert status["input_error"] is None
    assert status["last_decode_at"] == "2026-09-13T11:30:15+00:00"
    assert status["dial_frequency_hz"] == 21_074_000
    assert status["udp_recently_seen"] is False
    assert status["last_heartbeat_at"] is None
