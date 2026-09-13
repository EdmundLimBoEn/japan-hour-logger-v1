from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from sqlalchemy import func, select

from radio_logger.database.models import Observation
from radio_logger.database.repository import Repository
from radio_logger.models import RawDecode
from radio_logger.service import replay_file
from radio_logger.wsjtx.all_txt import parse_all_txt
from radio_logger.wsjtx.protocol import (
    MessageTypeId,
    PacketError,
    encode_decode,
    encode_header,
    encode_status,
    parse_packet,
)


UTC = timezone.utc


def _raw(*, source: str = "udp", when: datetime | None = None, dt: float = 0.2) -> RawDecode:
    return RawDecode(
        source=source,
        instance_id="A" if source == "udp" else None,
        decode_time_utc=when or datetime(2026, 9, 11, 1, 0, tzinfo=UTC),
        snr_db=-12,
        dt=dt,
        df=1234,
        mode="FT8",
        raw_message="CQ JA1XYZ PM95",
        dial_frequency_hz=14_074_000,
    )


def _observation_count(session_factory) -> int:
    session = session_factory()
    try:
        return int(session.scalar(select(func.count()).select_from(Observation)) or 0)
    finally:
        session.close()


def test_replay_keeps_identical_decodes_on_different_days(ingestor, tmp_path: Path):
    path = tmp_path / "ALL.TXT"
    path.write_text(
        "260911_010000    14.074 Rx FT8    -12  0.2 1234 CQ JA1XYZ PM95\n"
        "260912_010000    14.074 Rx FT8    -12  0.2 1234 CQ JA1XYZ PM95\n"
    )

    assert replay_file(ingestor, path, preserve_copy=False) == 2


def test_non_network_imports_are_not_suppressed(ingestor):
    raw = _raw(source="all_txt")

    assert ingestor.ingest_raw(raw) is not None
    assert ingestor.ingest_raw(raw.model_copy()) is not None


def test_direct_udp_ingest_dedupes_after_matching_status_fills_dial(ingestor):
    ingestor.handle_datagram(encode_status(instance_id="A", dial_frequency_hz=14_074_000))
    raw = _raw().model_copy(update={"dial_frequency_hz": None})

    assert ingestor.ingest_raw(raw) is not None
    assert ingestor.ingest_raw(raw.model_copy(update={"dial_frequency_hz": None})) is None


def test_fingerprint_preserves_full_timestamp_and_float_precision():
    first = _raw(dt=0.21)
    next_day = first.model_copy(update={"decode_time_utc": first.decode_time_utc + timedelta(days=1)})
    nearby_dt = first.model_copy(update={"dt": 0.24})

    assert first.fingerprint() != next_day.fingerprint()
    assert first.fingerprint() != nearby_dt.fingerprint()


def test_fingerprint_normalizes_equivalent_timestamp_offsets():
    utc = _raw()
    offset = utc.model_copy(
        update={"decode_time_utc": utc.decode_time_utc.astimezone(timezone(timedelta(hours=8)))}
    )

    assert utc.fingerprint() == offset.fingerprint()


def test_failed_store_can_be_retried(ingestor, monkeypatch):
    original = Repository.insert_observation
    calls = 0

    def fail_once(self, decoded, session_id=None):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise RuntimeError("temporary write failure")
        return original(self, decoded, session_id=session_id)

    monkeypatch.setattr(Repository, "insert_observation", fail_once)
    raw = _raw()

    assert ingestor.ingest_raw(raw) is None
    assert ingestor.ingest_raw(raw.model_copy()) is not None


def test_committed_decode_is_not_reported_failed_when_refresh_would_fail(
    ingestor, session_factory, monkeypatch
):
    def fail_refresh(*args, **kwargs):
        raise RuntimeError("refresh failed after commit")

    monkeypatch.setattr("sqlalchemy.orm.Session.refresh", fail_refresh)
    raw = _raw()

    row = ingestor.ingest_raw(raw)

    assert row is not None
    assert row.timestamp_utc == raw.decode_time_utc
    assert ingestor.ingest_raw(raw.model_copy()) is None
    assert _observation_count(session_factory) == 1


def test_decode_uses_status_from_its_own_instance(ingestor):
    ingestor.handle_datagram(encode_status(instance_id="A", dial_frequency_hz=14_074_000))
    ingestor.handle_datagram(encode_status(instance_id="B", dial_frequency_hz=7_074_000))

    row = ingestor.handle_datagram(encode_decode("CQ JA1XYZ PM95", instance_id="A"))

    assert row is not None
    assert row.dial_frequency_hz == 14_074_000


def test_decode_without_matching_status_has_no_dial_fallback(ingestor):
    ingestor.handle_datagram(encode_status(instance_id="B", dial_frequency_hz=7_074_000))

    row = ingestor.handle_datagram(encode_decode("CQ JA1XYZ PM95", instance_id="A"))

    assert row is not None
    assert row.dial_frequency_hz is None


def test_archive_failure_does_not_lose_decode_or_hide_committed_row(
    ingestor, config, session_factory, tmp_path: Path
):
    raw_dir = Path(config.paths.raw_dir)
    raw_dir.rmdir()
    raw_dir.write_text("not a directory")
    config.paths.jsonl_events = True

    row = ingestor.handle_datagram(encode_decode("CQ JA1XYZ PM95"))

    assert row is not None
    assert _observation_count(session_factory) == 1


def test_header_only_decode_is_rejected():
    packet = encode_header(MessageTypeId.DECODE, "WSJT-X").dumps()

    with pytest.raises(PacketError):
        parse_packet(packet)


def test_decode_rejects_invalid_qtime():
    packet = encode_decode("CQ JA1XYZ PM95", time_ms=86_400_000)

    with pytest.raises(PacketError):
        parse_packet(packet)


def test_decode_allows_missing_optional_trailing_flags():
    message = parse_packet(encode_decode("CQ JA1XYZ PM95")[:-2])

    assert message.low_confidence is False
    assert message.off_air is False


def test_live_wsjtx_ft8_symbol_is_normalized_at_ingest(ingestor):
    packet = bytes.fromhex(
        "adbccbda00000002000000020000000657534a542d580102efa5e0fffffffc"
        "3fc99999a00000000000071c000000017e0000000e435120594332554b4c20"
        "4f4935320000"
    )

    message = parse_packet(packet)
    row = ingestor.handle_datagram(packet)

    assert message.mode == "~"
    assert message.raw["mode"] == "~"
    assert row is not None
    assert row.mode == "FT8"
    assert json.loads(row.raw_payload)["mode"] == "~"


def test_replay_speed_preserves_original_delay(ingestor, tmp_path: Path, monkeypatch):
    path = tmp_path / "ALL.TXT"
    path.write_text(
        "260911_010000    14.074 Rx FT8    -12  0.2 1234 CQ JA1XYZ PM95\n"
        "260911_010015    14.074 Rx FT8    -12  0.2 1234 CQ JA1ABC PM95\n"
    )
    delays: list[float] = []
    monkeypatch.setattr("radio_logger.service.time.sleep", delays.append)

    assert replay_file(ingestor, path, speed=1, preserve_copy=False) == 2
    assert delays == [15.0]


def test_replay_continues_when_preserve_copy_fails(ingestor, config, tmp_path: Path):
    path = tmp_path / "input.txt"
    path.write_text("260911_010000    14.074 Rx FT8    -12  0.2 1234 CQ JA1XYZ PM95\n")
    raw_dir = Path(config.paths.raw_dir)
    raw_dir.rmdir()
    raw_dir.write_text("not a directory")

    assert replay_file(ingestor, path, preserve_copy=True) == 1


def test_compact_all_txt_does_not_invent_dial_frequency():
    text = "260911_010000 -12 0.2 1234 ~ CQ JA1XYZ PM95"

    assert parse_all_txt(text)[0].dial_frequency_hz is None
    assert parse_all_txt(text, default_dial_hz=7_074_000)[0].dial_frequency_hz == 7_074_000
