from __future__ import annotations

import os
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest
from sqlalchemy import select, text

from radio_logger.database.models import InputCursor, Observation
from radio_logger.ingest import Ingestor
from radio_logger.models import FilePosition, RuntimeState
from radio_logger.wsjtx import file_input
from radio_logger.wsjtx.file_input import AllTxtFollower


def line(second: int = 15) -> bytes:
    return f"260913_1130{second:02d}    21.074 Rx FT8    -15  0.0 1047 JQ1GNQ BG7HFE OL67\n".encode()


@pytest.mark.parametrize(
    "identifier",
    [-(2**63), 0, 2**63 - 1, 2**63, 2**64 - 1, 2**128 - 1],
    ids=["signed-min", "zero", "signed-max", "unsigned-min", "unsigned-max", "128-bit-max"],
)
def test_cursor_identifiers_round_trip_without_numeric_coercion(
    ingestor, session_factory, identifier
):
    position = FilePosition(identifier, identifier, "a" * 32, 0, "")
    assert ingestor.ingest_file_batch("source", None, position, [])
    restored = ingestor.file_position("source")
    assert restored == position
    assert type(restored.device) is int
    assert type(restored.inode) is int

    with session_factory() as session:
        assert session.scalar(
            select(InputCursor.source_path).where(
                InputCursor.device == identifier, InputCursor.inode == identifier
            )
        ) == "source"
        stored = session.execute(
            text("SELECT device, inode, typeof(device), typeof(inode) FROM input_cursors")
        ).one()
    if -(2**63) <= identifier < 2**63:
        assert tuple(stored) == (identifier, identifier, "integer", "integer")
    else:
        assert tuple(stored) == (f"id:{identifier}", f"id:{identifier}", "text", "text")


@pytest.mark.parametrize("field", ["device", "inode"])
def test_compare_and_swap_distinguishes_adjacent_large_identifiers(
    ingestor, field
):
    original = FilePosition(2**100, 2**128 - 2, "b" * 32, 12, "abcd")
    assert ingestor.ingest_file_batch("source", None, original, [])
    stale = replace(original, **{field: getattr(original, field) + 1})
    advanced = replace(original, device=original.device + 1, inode=original.inode + 1, offset=24)

    assert not ingestor.ingest_file_batch("source", stale, advanced, [])
    assert ingestor.file_position("source") == original
    assert "changed in another consumer" in ingestor.runtime.storage_error
    assert ingestor.ingest_file_batch("source", original, advanced, [])
    assert ingestor.file_position("source") == advanced
    assert ingestor.runtime.storage_error is None


def test_legacy_integer_cursor_can_advance_to_large_identifiers(ingestor, session_factory):
    original = FilePosition(17, 2**63 - 1, "c" * 32, 12, "abcd")
    with session_factory() as session:
        session.execute(
            text(
                "INSERT INTO input_cursors "
                "(receiver_id, source_path, device, inode, generation, offset, anchor) "
                "VALUES (:receiver_id, 'source', :device, :inode, :generation, :offset, :anchor)"
            ),
            {
                "receiver_id": ingestor.config.receiver.id,
                "device": original.device,
                "inode": original.inode,
                "generation": original.generation,
                "offset": original.offset,
                "anchor": original.anchor,
            },
        )
        session.commit()
    assert ingestor.file_position("source") == original

    advanced = replace(original, device=2**64 - 1, inode=2**128 - 1, offset=24)
    assert ingestor.ingest_file_batch("source", original, advanced, [])
    assert ingestor.file_position("source") == advanced
    restored = replace(original, offset=36)
    assert ingestor.ingest_file_batch("source", advanced, restored, [])
    assert ingestor.file_position("source") == restored


class StatWithIdentity:
    def __init__(self, stat, identity):
        self._stat = stat
        self.st_dev, self.st_ino = identity

    def __getattr__(self, name):
        return getattr(self._stat, name)


@pytest.fixture
def file_identities(monkeypatch):
    original_stat = Path.stat
    original_fstat = os.fstat
    identities = {}

    def with_identity(stat):
        identity = identities.get((stat.st_dev, stat.st_ino))
        return stat if identity is None else StatWithIdentity(stat, identity)

    def stat(path, *args, **kwargs):
        return with_identity(original_stat(path, *args, **kwargs))

    def register(path, device, inode):
        native = original_stat(path)
        identities[native.st_dev, native.st_ino] = (device, inode)

    monkeypatch.setattr(Path, "stat", stat)
    monkeypatch.setattr(
        file_input, "os", SimpleNamespace(fstat=lambda fd: with_identity(original_fstat(fd)))
    )
    return register


@pytest.mark.parametrize("device,inode", [(2**63, 2**64 - 1), (2**128 - 2, 2**128 - 1)])
def test_follower_restart_append_and_stale_cursor_with_large_identifiers(
    ingestor, session_factory, tmp_path, file_identities, device, inode
):
    path = tmp_path / "ALL.TXT"
    path.write_bytes(line() * 2)
    file_identities(path, device, inode)
    first = AllTxtFollower(path, ingestor)
    try:
        assert first.poll_once() == 2
        saved = first.position
        assert (saved.device, saved.inode) == (device, inode)
    finally:
        first.close()

    restarted = AllTxtFollower(path, ingestor)
    stale = AllTxtFollower(path, ingestor)
    try:
        assert restarted.position == saved
        assert restarted.poll_once() == 0
        assert stale.poll_once() == 0
        with path.open("ab") as handle:
            handle.write(line(second=30))
        assert restarted.poll_once() == 1
        assert restarted.poll_once() == 0
        with pytest.raises(RuntimeError, match="changed in another consumer"):
            stale.poll_once()
        assert stale.poll_once() == 0
        assert stale.position == restarted.position
        assert stale.position.offset == path.stat().st_size
        assert stale.position.generation == saved.generation
    finally:
        restarted.close()
        stale.close()
    with session_factory() as session:
        assert list(session.scalars(select(Observation.timestamp_utc).order_by(Observation.id))) == [
            datetime(2026, 9, 13, 11, 30, 15),
            datetime(2026, 9, 13, 11, 30, 15),
            datetime(2026, 9, 13, 11, 30, 30),
        ]


def test_follower_recovers_rotated_file_with_adjacent_128_bit_identifiers(
    ingestor, session_factory, tmp_path, file_identities
):
    path = tmp_path / "ALL.TXT"
    path.write_bytes(line())
    device, inode = 2**100, 2**128 - 2
    file_identities(path, device, inode)
    first = AllTxtFollower(path, ingestor)
    try:
        assert first.poll_once() == 1
        generation = first.position.generation
        with path.open("ab") as handle:
            handle.write(line(second=30))
    finally:
        first.close()
    path.rename(tmp_path / "ALL.TXT.1")
    path.write_bytes(line(second=45))
    file_identities(path, device, inode + 1)

    restarted = AllTxtFollower(path, ingestor)
    try:
        assert restarted.poll_once() == 1
        assert restarted.position.inode == inode
        assert restarted.position.generation == generation
        assert restarted.poll_once() == 1
        assert restarted.position.inode == inode + 1
        assert restarted.position.generation != generation
        assert restarted.poll_once() == 0
    finally:
        restarted.close()
    with session_factory() as session:
        timestamps = session.scalars(select(Observation.timestamp_utc).order_by(Observation.id))
        assert [timestamp.second for timestamp in timestamps] == [15, 30, 45]


def test_migration_adopts_populated_cursor_with_large_identifiers(config, tmp_path, monkeypatch):
    from alembic import command
    from alembic.config import Config

    from radio_logger.service import init_database

    root = Path(__file__).resolve().parents[1]
    migration_config = Config(str(root / "alembic.ini"))
    migration_config.set_main_option("script_location", str(root / "alembic"))
    monkeypatch.setenv("RADIO_LOGGER_DATABASE__URL", config.database.url)
    monkeypatch.chdir(tmp_path)
    command.upgrade(migration_config, "0001_initial")
    factory = init_database(config)
    ingestor = Ingestor(config, factory, RuntimeState(started_at=datetime.now(timezone.utc)))
    original = FilePosition(2**64 - 1, 2**128 - 1, "d" * 32, 12, "abcd")
    assert ingestor.ingest_file_batch("source", None, original, [])

    command.upgrade(migration_config, "head")
    command.upgrade(migration_config, "head")
    assert ingestor.file_position("source") == original
    advanced = replace(original, offset=24)
    assert ingestor.ingest_file_batch("source", original, advanced, [])
    assert ingestor.file_position("source") == advanced
    with factory() as session:
        columns = session.execute(text("PRAGMA table_info(input_cursors)")).mappings()
        types = {column["name"]: column["type"] for column in columns}
        assert types["device"] == types["inode"] == "BIGINT"
        assert session.execute(text("SELECT version_num FROM alembic_version")).scalar_one() == (
            "0002_input_cursors"
        )
