from __future__ import annotations

import json
import logging
import time
from copy import deepcopy
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from shutil import copy2

from sqlalchemy import update
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.exc import OperationalError

from radio_logger.bands import band_from_hz
from radio_logger.config import AppConfig
from radio_logger.database.models import InputCursor, Observation
from radio_logger.database.repository import Repository
from radio_logger.enrichment.pipeline import Enricher, enrich_or_passthrough
from radio_logger.models import FilePosition, NormalizedDecode, RawDecode, ReceiverStatus, RuntimeState
from radio_logger.wsjtx.dedupe import NetworkDedupe
from radio_logger.wsjtx.protocol import (
    ClearMessage,
    CloseMessage,
    DecodeMessage,
    HeartbeatMessage,
    StatusMessage,
    WsjtxMessage,
    parse_packet,
)

log = logging.getLogger(__name__)


class Ingestor:
    def __init__(
        self,
        config: AppConfig,
        session_factory: sessionmaker[Session],
        runtime: RuntimeState,
        *,
        enricher: Enricher | None = None,
        dedupe: NetworkDedupe | None = None,
    ):
        self.config = config
        self.session_factory = session_factory
        self.runtime = runtime
        self.enricher = enricher or Enricher(config)
        self.dedupe = dedupe or NetworkDedupe(config.dedupe.window_seconds)
        self.active_session_id: int | None = None
        self._sender_addresses: dict[str, tuple[str, int]] = {}
        self._ensure_receiver()

    def _ensure_receiver(self) -> None:
        session = self.session_factory()
        try:
            repo = Repository(session)
            repo.upsert_receiver(
                self.config.receiver.id,
                self.config.receiver.name,
                self.config.receiver_grid,
                self.config.receiver.timezone,
            )
            rx_session = repo.open_session(self.config.receiver.id, mode="FT8")
            self.active_session_id = rx_session.id
            repo.add_event("startup", "logger session opened", receiver_id=self.config.receiver.id)
            session.commit()
        finally:
            session.close()

    def handle_datagram(
        self, data: bytes, addr: tuple[str, int] | None = None,
        *, received_at: datetime | None = None, received_monotonic: float | None = None,
    ) -> Observation | None:
        received_at = received_at or datetime.now(tz=timezone.utc)
        self._archive_optional(self._archive_raw_bytes, data)
        try:
            message = parse_packet(data, received_at=received_at)
        except Exception as exc:
            self.runtime.parse_errors += 1
            self.runtime.last_error = f"udp parse: {exc}"
            if self.runtime.parse_errors == 1 or self.runtime.parse_errors % 100 == 0:
                self._event("udp_parse_error", str(exc), level="warning")
            return None
        self.runtime.last_udp_at = received_at
        self.runtime.udp_error = None
        if addr is not None and message.instance_id:
            previous = self._sender_addresses.get(message.instance_id)
            if previous is not None and previous != addr:
                self._forget_status(message.instance_id)
            if message.instance_id not in self._sender_addresses and len(self._sender_addresses) >= 64:
                self._forget_status(next(iter(self._sender_addresses)))
            self._sender_addresses[message.instance_id] = addr
        return self.handle_wsjtx_message(
            message, received_at=received_at, received_monotonic=received_monotonic,
        )

    def handle_wsjtx_message(
        self, message: WsjtxMessage, *, received_at: datetime | None = None,
        received_monotonic: float | None = None,
    ) -> Observation | None:
        now = received_at or datetime.now(tz=timezone.utc)
        if isinstance(message, HeartbeatMessage):
            self.runtime.last_heartbeat_at = now
            if message.instance_id:
                status = self._receiver_status(message.instance_id)
                self.runtime.receiver_status = status
            return None
        if isinstance(message, StatusMessage):
            self._apply_status(message, now)
            return None
        if isinstance(message, ClearMessage):
            self.runtime.last_clear_at = now
            self._event("wsjtx_clear", f"clear from {message.instance_id}")
            return None
        if isinstance(message, CloseMessage):
            self.runtime.last_close_at = now
            self._forget_status(message.instance_id)
            self._sender_addresses.pop(message.instance_id, None)
            self._event("wsjtx_close", f"close from {message.instance_id}")
            return None
        if isinstance(message, DecodeMessage):
            if not message.is_new or message.off_air:
                self.runtime.ignored_decodes += 1
                return None
            status = self.runtime.receiver_statuses.get(message.instance_id)
            if status is not None and status.updated_at is not None:
                age = (now - status.updated_at).total_seconds()
                if age < 0 or age > 120:
                    self._forget_status(message.instance_id)
                    status = None
            return self.ingest_raw(_decode_to_raw(message, status), dedupe_now=received_monotonic)
        return None

    def _forget_status(self, instance_id: str) -> None:
        self.runtime.receiver_statuses.pop(instance_id, None)
        self._sender_addresses.pop(instance_id, None)
        if self.runtime.receiver_status.instance_id == instance_id:
            self.runtime.receiver_status = ReceiverStatus()

    def _receiver_status(self, instance_id: str) -> ReceiverStatus:
        statuses = self.runtime.receiver_statuses
        if instance_id not in statuses:
            if len(statuses) >= 64:
                self._forget_status(next(iter(statuses)))
            statuses[instance_id] = ReceiverStatus(instance_id=instance_id)
        return statuses[instance_id]

    def ingest_raw(
        self, raw: RawDecode, *, skip_dedupe: bool = False, dedupe_now: float | None = None,
    ) -> Observation | None:
        if raw.dial_frequency_hz is None and raw.source == "udp" and raw.instance_id:
            status = self.runtime.receiver_statuses.get(raw.instance_id)
            if status is not None:
                raw.dial_frequency_hz = status.dial_frequency_hz
        use_network_dedupe = raw.source == "udp" and not skip_dedupe
        if use_network_dedupe and self.dedupe.contains(raw, now=dedupe_now):
            self.runtime.duplicate_suppressed += 1
            return None
        normalized = enrich_or_passthrough(self.enricher, raw)
        session = self.session_factory()
        session.expire_on_commit = False
        try:
            repo = Repository(session)
            for attempt in range(3):
                try:
                    row = repo.insert_observation(normalized, session_id=self.active_session_id)
                    session.commit()
                    break
                except OperationalError as exc:
                    session.rollback()
                    self.runtime.storage_error = str(exc)
                    if attempt == 2:
                        raise
                    time.sleep(0.1 * (attempt + 1))
        except Exception as exc:
            session.rollback()
            self.runtime.last_error = f"store: {exc}"
            self.runtime.storage_error = str(exc)
            self.runtime.storage_failures += 1
            log.exception("failed to store decode")
            return None
        finally:
            session.close()
        if use_network_dedupe:
            self.dedupe.remember(raw, now=dedupe_now)
        self.runtime.storage_error = None
        self.runtime.decode_count_session += 1
        self.runtime.last_decode_at = normalized.timestamp_utc
        self.runtime.last_message = normalized.raw_message
        self._archive_optional(self._append_jsonl, normalized.model_dump(mode="json"))
        return row

    def file_position(self, source_path: str) -> FilePosition | None:
        with self.session_factory() as session:
            row = session.get(InputCursor, (self.config.receiver.id, source_path))
            if row is None:
                return None
            return FilePosition(**{name: getattr(row, name) for name in FilePosition.__dataclass_fields__})

    def ingest_file_batch(
        self,
        source_path: str,
        expected: FilePosition | None,
        position: FilePosition,
        decodes: list[RawDecode],
    ) -> bool:
        cache_before = self.enricher.stations
        self.enricher.stations = deepcopy(cache_before)
        normalized = [enrich_or_passthrough(self.enricher, raw) for raw in decodes]
        with self.session_factory() as session:
            try:
                if expected is None:
                    session.add(InputCursor(
                        receiver_id=self.config.receiver.id,
                        source_path=source_path,
                        **asdict(position),
                    ))
                    session.flush()
                else:
                    result = session.execute(
                        update(InputCursor)
                        .where(
                            InputCursor.receiver_id == self.config.receiver.id,
                            InputCursor.source_path == source_path,
                            *[getattr(InputCursor, name) == value for name, value in asdict(expected).items()],
                        )
                        .values(**asdict(position))
                    )
                    if result.rowcount != 1:
                        raise RuntimeError("file cursor changed in another consumer")
                repo = Repository(session)
                for decoded in normalized:
                    repo.insert_observation(decoded, session_id=self.active_session_id)
                session.commit()
            except Exception as exc:
                session.rollback()
                self.enricher.stations = cache_before
                self.runtime.last_error = f"file store: {exc}"
                self.runtime.storage_error = str(exc)
                self.runtime.storage_failures += 1
                log.exception("failed to commit file input batch")
                return False
        self.runtime.storage_error = None
        for decoded in normalized:
            self._after_commit(decoded)
            status = self.runtime.receiver_status
            status.dial_frequency_hz = decoded.dial_frequency_hz
            status.mode = decoded.mode
            status.updated_at = datetime.now(tz=timezone.utc)
        if self.runtime.last_error and self.runtime.last_error.startswith("file store:"):
            self.runtime.last_error = None
        return True

    def _after_commit(self, normalized: NormalizedDecode) -> None:
        self.runtime.decode_count_session += 1
        if self.runtime.last_decode_at is None or normalized.timestamp_utc >= self.runtime.last_decode_at:
            self.runtime.last_decode_at = normalized.timestamp_utc
            self.runtime.last_message = normalized.raw_message
        self._archive_optional(self._append_jsonl, normalized.model_dump(mode="json"))

    def _apply_status(self, message: StatusMessage, now: datetime) -> None:
        status = self._receiver_status(message.instance_id)
        status.dial_frequency_hz = message.dial_frequency_hz
        status.mode = message.mode
        status.de_call = message.de_call
        status.de_grid = message.de_grid
        status.dx_call = message.dx_call
        status.dx_grid = message.dx_grid
        status.decoding = message.decoding
        status.transmitting = message.transmitting
        status.instance_id = message.instance_id
        status.updated_at = now
        self.runtime.receiver_status = status
        if message.dial_frequency_hz and self.active_session_id:
            session = self.session_factory()
            try:
                from radio_logger.database.models import Session as RxSession

                row = session.get(RxSession, self.active_session_id)
                if row is not None:
                    row.dial_frequency_hz = message.dial_frequency_hz
                    row.band = band_from_hz(message.dial_frequency_hz)
                    row.mode = message.mode
                    row.instance_id = message.instance_id
                    session.commit()
            except Exception as exc:
                session.rollback()
                self.runtime.last_error = f"status: {exc}"
                log.warning("Could not save receiver status: %s", exc)
            finally:
                session.close()

    def close(self) -> None:
        if self.active_session_id is None:
            return
        from radio_logger.database.models import Session as RxSession

        session = self.session_factory()
        try:
            row = session.get(RxSession, self.active_session_id)
            if row is not None:
                row.ended_at = datetime.now(tz=timezone.utc)
                session.commit()
            self.active_session_id = None
        except Exception:
            session.rollback()
            log.exception("Could not close receiver session")
        finally:
            session.close()

    def _event(self, event_type: str, message: str, level: str = "info") -> None:
        session = self.session_factory()
        try:
            Repository(session).add_event(
                event_type, message, level=level, receiver_id=self.config.receiver.id
            )
            session.commit()
        except Exception:
            session.rollback()
        finally:
            session.close()

    def _archive_raw_bytes(self, data: bytes) -> None:
        if not self.config.paths.jsonl_events:
            return
        now = datetime.now(tz=timezone.utc)
        path = Path(self.config.paths.raw_dir) / now.strftime("%Y") / now.strftime("%m")
        path.mkdir(parents=True, exist_ok=True)
        line = json.dumps(
            {
                "ts": now.isoformat(),
                "kind": "udp",
                "bytes_hex": data.hex(),
                "size": len(data),
            }
        )
        with (path / "udp-events.jsonl").open("a", encoding="utf-8") as handle:
            handle.write(line + "\n")

    def _archive_optional(self, operation, payload) -> None:
        try:
            operation(payload)
        except OSError as exc:
            self.runtime.last_error = f"archive: {exc}"
            log.warning("optional raw archive failed: %s", exc)

    def _append_jsonl(self, payload: dict) -> None:
        if not self.config.paths.jsonl_events:
            return
        ts = datetime.now(tz=timezone.utc)
        path = Path(self.config.paths.raw_dir) / ts.strftime("%Y") / ts.strftime("%m")
        path.mkdir(parents=True, exist_ok=True)
        with (path / "decodes.jsonl").open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(payload) + "\n")


def _decode_to_raw(message: DecodeMessage, status: ReceiverStatus | None) -> RawDecode:
    when = message.decode_time_utc or datetime.now(tz=timezone.utc)
    return RawDecode(
        source="udp",
        instance_id=message.instance_id,
        decode_time_utc=when,
        snr_db=float(message.snr),
        dt=float(message.dt),
        df=int(message.df),
        mode="FT8" if message.mode == "~" else message.mode or "FT8",
        raw_message=message.message,
        low_confidence=message.low_confidence,
        off_air=message.off_air,
        is_new=message.is_new,
        dial_frequency_hz=status.dial_frequency_hz if status is not None else None,
        raw_payload=message.raw,
    )


def preserve_all_txt_copy(src: Path, raw_dir: Path) -> Path:
    now = datetime.now(tz=timezone.utc)
    dest_dir = raw_dir / now.strftime("%Y") / now.strftime("%m")
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / src.name
    if dest.exists():
        dest = dest_dir / f"{src.stem}-{now.strftime('%H%M%S')}{src.suffix}"
    copy2(src, dest)
    return dest
