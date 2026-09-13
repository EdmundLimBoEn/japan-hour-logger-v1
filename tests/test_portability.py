from __future__ import annotations

import asyncio
import socket
import sqlite3
import zoneinfo
from datetime import datetime, timedelta, timezone
from importlib import resources

import pytest

import radio_logger.database.backup as backups
from radio_logger.config import UdpConfig
from radio_logger.timeutil import to_local
from radio_logger.wsjtx.listener import start_udp_listener


def test_timezone_conversion_without_system_timezone_database():
    original_path = zoneinfo.TZPATH
    zoneinfo.reset_tzpath([])
    zoneinfo.ZoneInfo.clear_cache()
    try:
        local = to_local(datetime(2026, 9, 14, 0, 0, tzinfo=timezone.utc))
        assert local.hour == 8
        assert local.utcoffset() == timedelta(hours=8)
        assert to_local(local, "UTC").hour == 0
    finally:
        zoneinfo.reset_tzpath(original_path)
        zoneinfo.ZoneInfo.clear_cache()


@pytest.mark.parametrize(
    "name",
    [
        "resources/sample_all.txt",
        "resources/cty.dat",
        "web/templates/index.html",
        "web/static/app.js",
        "web/static/app.css",
        "web/static/chart.umd.min.js",
    ],
)
def test_runtime_resources_are_bundled(name):
    assert resources.files("radio_logger").joinpath(name).read_bytes()


@pytest.mark.asyncio
async def test_udp_listener_works_without_reuseport_and_receives_datagrams(monkeypatch):
    monkeypatch.delattr(socket, "SO_REUSEPORT", raising=False)
    received = asyncio.get_running_loop().create_future()
    transport, _bound = await start_udp_listener(
        UdpConfig(port=0), lambda data, addr: received.set_result((data, addr))
    )
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sender:
            sender.sendto(b"WSJT-X portability check", transport.get_extra_info("sockname"))
        data, addr = await asyncio.wait_for(received, timeout=2)
        assert data == b"WSJT-X portability check"
        assert addr[0] == "127.0.0.1"
    finally:
        transport.close()
        await asyncio.sleep(0)


@pytest.mark.asyncio
async def test_duplicate_unicast_listener_fails_and_closes_socket(monkeypatch):
    created = []
    original_socket = socket.socket

    class TrackedSocket(original_socket):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            created.append(self)

    monkeypatch.setattr(socket, "socket", TrackedSocket)
    transport, _bound = await start_udp_listener(UdpConfig(port=0), lambda *_: None)
    try:
        port = transport.get_extra_info("sockname")[1]
        with pytest.raises(OSError):
            await start_udp_listener(UdpConfig(port=port), lambda *_: None)
        assert len(created) == 2
        assert created[-1].fileno() == -1
    finally:
        transport.close()
        await asyncio.sleep(0)


def test_backup_never_creates_a_missing_source(tmp_path):
    source = tmp_path / "missing #1 with spaces.db"
    with pytest.raises(sqlite3.OperationalError):
        backups.backup_sqlite(f"sqlite:///{source.as_posix()}", tmp_path / "backups")
    assert not source.exists()
    assert not list((tmp_path / "backups").glob("*.db"))


def test_backup_preserves_existing_snapshot_on_filename_collision(tmp_path, monkeypatch):
    source = tmp_path / "radio #1 with spaces.db"
    with sqlite3.connect(source) as connection:
        connection.execute("CREATE TABLE observations (message TEXT)")
        connection.execute("INSERT INTO observations VALUES ('CQ JA1XYZ PM95')")

    class FrozenClock:
        @staticmethod
        def now(tz):
            return datetime(2026, 9, 14, 0, 0, 0, 123456, tzinfo=tz)

    monkeypatch.setattr(backups, "datetime", FrozenClock)
    dest = tmp_path / "backups"
    first = backups.backup_sqlite(f"sqlite:///{source.as_posix()}", dest)
    original = first.read_bytes()
    with sqlite3.connect(source) as connection:
        connection.execute("INSERT INTO observations VALUES ('CQ VK2ABC QF56')")
    with pytest.raises(FileExistsError):
        backups.backup_sqlite(f"sqlite:///{source.as_posix()}", dest)
    assert first.read_bytes() == original
    with sqlite3.connect(first) as connection:
        assert connection.execute("PRAGMA integrity_check").fetchone() == ("ok",)
        assert connection.execute("SELECT COUNT(*) FROM observations").fetchone() == (1,)
