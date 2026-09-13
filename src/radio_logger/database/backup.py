from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path


def sqlite_path_from_url(url: str) -> Path | None:
    if not url.startswith("sqlite:///"):
        return None
    path = url.removeprefix("sqlite:///")
    if path == ":memory:":
        return None
    return Path(path)


def backup_sqlite(url: str, dest_dir: str | Path) -> Path:
    src = sqlite_path_from_url(url)
    if src is None:
        raise ValueError("Online backup is only implemented for on-disk SQLite")
    dest_root = Path(dest_dir)
    dest_root.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(tz=timezone.utc).strftime("%Y%m%dT%H%M%S.%fZ")
    dest = dest_root / f"radio-{stamp}.db"
    source = sqlite3.connect(f"{src.resolve().as_uri()}?mode=ro", uri=True)
    try:
        dest.touch(exist_ok=False)
        target = sqlite3.connect(dest)
        try:
            source.backup(target)
        finally:
            target.close()
    finally:
        source.close()
    return dest
