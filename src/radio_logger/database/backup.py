from __future__ import annotations

import math
import os
import sqlite3
import tempfile
import time
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy.engine import make_url


def sqlite_path_from_url(url: str) -> Path | None:
    parsed = make_url(url)
    if parsed.get_backend_name() != "sqlite":
        return None
    path = parsed.database
    if not path or path == ":memory:" or parsed.query.get("mode") == "memory":
        return None
    if parsed.query.get("uri") == "true" and path.startswith("file:"):
        from urllib.parse import unquote, urlsplit

        uri = urlsplit(path)
        path = unquote(uri.path)
        if uri.netloc:
            path = f"//{uri.netloc}{path}"
        elif os.name == "nt" and len(path) > 2 and path[0] == "/" and path[2] == ":":
            path = path[1:]
    return Path(path)


def backup_sqlite(url: str, dest_dir: str | Path, *, timeout_seconds: float = 30.0) -> Path:
    if not math.isfinite(timeout_seconds) or timeout_seconds <= 0:
        raise ValueError("Backup timeout must be a finite positive number of seconds")
    src = sqlite_path_from_url(url)
    if src is None:
        raise ValueError("Online backup is only implemented for on-disk SQLite")
    dest_root = Path(dest_dir)
    dest_root.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(tz=timezone.utc).strftime("%Y%m%dT%H%M%S.%fZ")
    dest = dest_root / f"radio-{stamp}.db"
    if dest.exists():
        raise FileExistsError(dest)
    deadline = time.monotonic() + timeout_seconds

    def check_deadline(_status: int, _remaining: int, _total: int) -> None:
        if time.monotonic() >= deadline:
            raise TimeoutError(f"Database backup exceeded {timeout_seconds:g} seconds")

    temporary: Path | None = None
    try:
        with closing(sqlite3.connect(
            f"{src.resolve().as_uri()}?mode=ro", uri=True, timeout=min(timeout_seconds, 0.1)
        )) as source:
            descriptor, name = tempfile.mkstemp(prefix=f".{dest.stem}-", suffix=".partial", dir=dest_root)
            temporary = Path(name)
            os.close(descriptor)
            with closing(sqlite3.connect(temporary)) as target:
                source.backup(target, pages=256, progress=check_deadline, sleep=0.05)
                target.set_progress_handler(lambda: int(time.monotonic() >= deadline), 1000)
                try:
                    result = target.execute("PRAGMA quick_check").fetchall()
                finally:
                    target.set_progress_handler(None, 0)
                if result != [("ok",)]:
                    raise sqlite3.DatabaseError(f"Backup integrity check failed: {result!r}")
                check_deadline(0, 0, 0)
                # A backup must be self-contained when copied without SQLite's sidecar files.
                target.execute("PRAGMA journal_mode=DELETE")
        with temporary.open("r+b") as handle:
            os.fsync(handle.fileno())
        if os.name == "nt":
            os.rename(temporary, dest)
        else:
            os.link(temporary, dest)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
    return dest
