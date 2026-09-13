from __future__ import annotations

import os
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest


pytestmark = pytest.mark.skipif(
    sys.platform == "win32", reason="The Mac pull helper uses POSIX SSH/rsync executables and symlinks"
)


def _executable(path: Path, source: str) -> None:
    path.write_text(source)
    path.chmod(0o755)


def test_pull_helper_installs_verified_snapshot_and_incrementally_syncs_raw(tmp_path):
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    _executable(
        fake_bin / "ssh",
        """#!/bin/sh
printf '%s\n' "$@" > "$SSH_ARGS"
printf '%s\n' 'backup /var/lib/japan-hour-logger/backups/radio-test.db'
""",
    )
    _executable(
        fake_bin / "scp",
        """#!/usr/bin/env python3
import sqlite3
import sys

connection = sqlite3.connect(sys.argv[-1])
connection.execute("CREATE TABLE observations (timestamp_utc TEXT)")
connection.executemany(
    "INSERT INTO observations VALUES (?)",
    [("2026-09-11 01:00:00",), ("2026-09-11 02:00:00",)],
)
connection.commit()
connection.close()
""",
    )
    _executable(
        fake_bin / "rsync",
        "#!/bin/sh\nprintf '%s\\n' \"$@\" > \"$RSYNC_ARGS\"\n",
    )
    destination = tmp_path / "radio data #1?"
    ssh_args = tmp_path / "ssh-args"
    rsync_args = tmp_path / "rsync-args"
    environment = {
        **os.environ,
        "PATH": f"{fake_bin}{os.pathsep}{os.environ['PATH']}",
        "SSH_ARGS": str(ssh_args),
        "RSYNC_ARGS": str(rsync_args),
    }

    result = subprocess.run(
        [
            sys.executable,
            "scripts/pull-radio-data",
            "--remote",
            "root@test-pi",
            "--destination",
            str(destination),
        ],
        check=True,
        capture_output=True,
        text=True,
        env=environment,
    )

    latest = destination / "latest.db"
    assert latest.is_symlink()
    with sqlite3.connect(f"{latest.resolve().as_uri()}?mode=ro", uri=True) as connection:
        assert connection.execute("PRAGMA integrity_check").fetchone() == ("ok",)
        assert connection.execute("SELECT COUNT(*) FROM observations").fetchone() == (2,)
    assert "Observations: 2" in result.stdout
    assert "UTC range: 2026-09-11 01:00:00 to 2026-09-11 02:00:00" in result.stdout
    remote_command = ssh_args.read_text()
    assert "root@test-pi" in remote_command
    assert "runuser -u radio-logger" in remote_command
    assert "/opt/japan-hour-logger/venv/bin/radio-logger db-backup" in remote_command
    arguments = rsync_args.read_text().splitlines()
    assert "-z" not in arguments
    assert "--delete" not in arguments
    assert "root@test-pi:/var/lib/japan-hour-logger/raw/wsjtx/" in arguments
