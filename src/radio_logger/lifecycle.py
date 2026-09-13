from __future__ import annotations

import os
from contextlib import contextmanager
from pathlib import Path

from radio_logger.config import AppConfig
from radio_logger.database.backup import sqlite_path_from_url


def logger_lock(config: AppConfig):
    database = sqlite_path_from_url(config.database.url)
    path = (
        Path(str(database.resolve()) + ".logger.lock")
        if database is not None
        else Path(config.paths.data_dir) / ".logger.lock"
    )
    return file_lock(path)


@contextmanager
def file_lock(path: Path):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a+b") as handle:
        try:
            if os.name == "nt":
                import msvcrt

                if path.stat().st_size == 0:
                    handle.write(b"0")
                    handle.flush()
                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            raise RuntimeError(
                f"This database is already in use by another logger or setup process: {path}. "
                "Use the existing console or stop it before starting another copy."
            ) from exc
        yield
