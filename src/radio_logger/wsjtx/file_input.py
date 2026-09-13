from __future__ import annotations

import asyncio
import logging
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import BinaryIO
from uuid import uuid4

from radio_logger.ingest import Ingestor
from radio_logger.models import FilePosition, RawDecode
from radio_logger.wsjtx.all_txt import iter_all_txt_lines

log = logging.getLogger(__name__)
MAX_BATCH_BYTES = 65_536
MAX_BATCH_LINES = 256
ANCHOR_BYTES = 256


class AllTxtFollower:
    def __init__(self, path: Path, ingestor: Ingestor):
        self.path = path.resolve()
        self.ingestor = ingestor
        self.source_path = str(self.path)
        self.position = ingestor.file_position(self.source_path)
        self._handle: BinaryIO | None = None
        self._working: FilePosition | None = None
        self.state = ingestor.runtime.file_input
        self.state.path = self.source_path

    def close(self) -> None:
        if self._handle is not None:
            self._handle.close()
        self._handle = None
        self._working = None

    @staticmethod
    def _identity(stat: os.stat_result) -> tuple[int, int]:
        return stat.st_dev, stat.st_ino

    @staticmethod
    def _matches(handle: BinaryIO, position: FilePosition) -> bool:
        if os.fstat(handle.fileno()).st_size < position.offset:
            return False
        anchor = bytes.fromhex(position.anchor)
        handle.seek(position.offset - len(anchor))
        return handle.read(len(anchor)) == anchor

    @staticmethod
    def _at(handle: BinaryIO, offset: int, generation: str) -> FilePosition:
        stat = os.fstat(handle.fileno())
        handle.seek(max(0, offset - ANCHOR_BYTES))
        anchor = handle.read(min(offset, ANCHOR_BYTES))
        return FilePosition(stat.st_dev, stat.st_ino, generation, offset, anchor.hex())

    def _warn(self, message: str) -> None:
        if self.state.recovery_warning != message:
            log.warning("%s: %s", self.source_path, message)
            self.ingestor._event("file_input_recovery", message, level="warning")
        self.state.recovery_warning = message

    def _open(self) -> None:
        self.close()
        position = self.position
        archives = sorted(
            path for path in self.path.parent.glob(self.path.name + ".*")
            if path.is_file() and path.suffix.lower() not in {".gz", ".bz2", ".xz", ".zst"}
        )
        candidates = [self.path, *archives]
        if position is not None:
            for path in candidates:
                try:
                    handle = path.open("rb")
                except FileNotFoundError:
                    continue
                stat = os.fstat(handle.fileno())
                if self._identity(stat) == (position.device, position.inode) and self._matches(handle, position):
                    self._handle = handle
                    self._working = position
                    return
                handle.close()
            if position.anchor:
                for path in archives:
                    with path.open("rb") as handle:
                        if self._matches(handle, position):
                            self._handle = path.open("rb")
                            self._working = self._at(self._handle, position.offset, position.generation)
                            self._warn("source was replaced or truncated; recovering its retained archive")
                            return
        self._handle = self.path.open("rb")
        self._working = self._at(self._handle, 0, uuid4().hex)
        if position is not None:
            self._warn("saved source bytes are unavailable or changed; resumed current file from zero; a recovery gap is possible")

    def _switch_if_rotated(self, partial_bytes: int) -> bool:
        assert self._handle is not None
        active = self.path.stat()
        opened = os.fstat(self._handle.fileno())
        if self._identity(active) == self._identity(opened):
            return False
        if partial_bytes:
            self._warn("rotated source ends with an incomplete line; the incomplete line was not imported")
        handle = self.path.open("rb")
        self._handle.close()
        self._handle = handle
        self._working = self._at(handle, 0, uuid4().hex)
        return True

    def poll_once(self) -> int:
        try:
            count = self._poll()
        except Exception as exc:
            self.state.readable = False
            self.state.error = str(exc)
            raise
        self.state.readable = True
        self.state.error = None
        self.state.last_poll_at = datetime.now(tz=timezone.utc)
        return count

    def _poll(self) -> int:
        if self._handle is None:
            self._open()
        assert self._handle is not None and self._working is not None
        if not self._matches(self._handle, self._working):
            self._open()
        for _ in range(2):
            assert self._handle is not None and self._working is not None
            handle = self._handle
            working = self._working
            handle.seek(working.offset)
            data = handle.read(MAX_BATCH_BYTES)
            newline = data.rfind(b"\n")
            if newline < 0:
                if self._switch_if_rotated(len(data)):
                    continue
                if len(data) == MAX_BATCH_BYTES:
                    raise ValueError(f"input line exceeds {MAX_BATCH_BYTES} bytes at offset {working.offset}")
                self.state.offset = working.offset
                self.state.size = os.fstat(handle.fileno()).st_size
                return 0
            lines = [part + b"\n" for part in data[:newline].split(b"\n")[:MAX_BATCH_LINES]]
            offset = working.offset
            decodes: list[RawDecode] = []
            for line in lines:
                parsed = list(iter_all_txt_lines([line.decode("utf-8", errors="replace")]))
                for raw in parsed:
                    raw.raw_payload.update({
                        "all_txt_path": self.source_path,
                        "all_txt_offset": offset,
                        "all_txt_generation": working.generation,
                    })
                    decodes.append(raw)
                offset += len(line)
            if not self._matches(handle, working):
                self._open()
                return 0
            position = self._at(handle, offset, working.generation)
            if not self.ingestor.ingest_file_batch(self.source_path, self.position, position, decodes):
                self.position = self.ingestor.file_position(self.source_path)
                self.close()
                raise RuntimeError(self.ingestor.runtime.last_error or "file input transaction failed")
            self.position = position
            self._working = position
            self.state.offset = position.offset
            self.state.size = os.fstat(handle.fileno()).st_size
            return len(decodes)
        return 0

    async def run(self, poll_seconds: float) -> None:
        try:
            while True:
                previous = self.position
                try:
                    self.poll_once()
                except Exception:
                    log.exception("could not read ALL.TXT input %s", self.source_path)
                    await asyncio.sleep(poll_seconds)
                    continue
                progressed = self.position != previous
                await asyncio.sleep(0 if progressed and self.state.size > self.state.offset else poll_seconds)
        finally:
            self.close()
