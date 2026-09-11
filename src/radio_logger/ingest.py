from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from shutil import copy2

from sqlalchemy.orm import Session, sessionmaker

from radio_logger.bands import band_from_hz
from radio_logger.config import AppConfig
from radio_logger.database.models import Observation
from radio_logger.database.repository import Repository
from radio_logger.enrichment.pipeline import Enricher, enrich_or_passthrough
from radio_logger.models import RawDecode, ReceiverStatus, RuntimeState
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

    def handle_datagram(self, data: bytes, addr: tuple[str, int] | None = None) -> Observation | None:
        self.runtime.last_udp_at = datetime.now(tz=timezone.utc)
        self._archive_raw_bytes(data)
        try:
            message = parse_packet(data)
        except Exception as exc:
            self.runtime.last_error = f"udp parse: {exc}"
            self._event("udp_parse_error", str(exc), level="warning")
            return None
        return self.handle_wsjtx_message(message)

    def handle_wsjtx_message(self, message: WsjtxMessage) -> Observation | None:
        now = datetime.now(tz=timezone.utc)
        if isinstance(message, HeartbeatMessage):
            self.runtime.last_heartbeat_at = now
            if message.instance_id:
                self.runtime.receiver_status.instance_id = message.instance_id
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
            self._event("wsjtx_close", f"close from {message.instance_id}")
            return None
        if isinstance(message, DecodeMessage):
            return self.ingest_raw(_decode_to_raw(message, self.runtime.receiver_status))
        return None

    def ingest_raw(self, raw: RawDecode, *, skip_dedupe: bool = False) -> Observation | None:
        if not skip_dedupe and self.dedupe.is_duplicate(raw):
            self.runtime.duplicate_suppressed += 1
            return None
        if raw.dial_frequency_hz is None and self.runtime.receiver_status.dial_frequency_hz:
            raw.dial_frequency_hz = self.runtime.receiver_status.dial_frequency_hz
        normalized = enrich_or_passthrough(self.enricher, raw)
        session = self.session_factory()
        try:
            repo = Repository(session)
            row = repo.insert_observation(normalized, session_id=self.active_session_id)
            session.commit()
            session.refresh(row)
            self.runtime.decode_count_session += 1
            self.runtime.last_decode_at = normalized.timestamp_utc
            self.runtime.last_message = normalized.raw_message
            self._append_jsonl(normalized.model_dump(mode="json"))
            return row
        except Exception as exc:
            session.rollback()
            self.runtime.last_error = f"store: {exc}"
            log.exception("failed to store decode")
            return None
        finally:
            session.close()

    def _apply_status(self, message: StatusMessage, now: datetime) -> None:
        status = self.runtime.receiver_status
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
        if message.de_grid and self.config.receiver_grid is None:
            # Status grid is the operator's locator from WSJT-X, not invented.
            # We do not silently overwrite config; we only cache on runtime status.
            pass
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

    def _append_jsonl(self, payload: dict) -> None:
        if not self.config.paths.jsonl_events:
            return
        ts = datetime.now(tz=timezone.utc)
        path = Path(self.config.paths.raw_dir) / ts.strftime("%Y") / ts.strftime("%m")
        path.mkdir(parents=True, exist_ok=True)
        with (path / "decodes.jsonl").open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(payload) + "\n")


def _decode_to_raw(message: DecodeMessage, status: ReceiverStatus) -> RawDecode:
    when = message.decode_time_utc or datetime.now(tz=timezone.utc)
    return RawDecode(
        source="udp",
        instance_id=message.instance_id,
        decode_time_utc=when,
        snr_db=float(message.snr),
        dt=float(message.dt),
        df=int(message.df),
        mode=message.mode or "FT8",
        raw_message=message.message,
        low_confidence=message.low_confidence,
        off_air=message.off_air,
        is_new=message.is_new,
        dial_frequency_hz=status.dial_frequency_hz,
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
