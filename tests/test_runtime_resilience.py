from __future__ import annotations

from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from radio_logger.api.app import create_app
from radio_logger.config import AppConfig, load_config
from radio_logger.database.repository import Repository
from radio_logger.wsjtx.protocol import MessageTypeId, encode_decode, encode_header, encode_status
from radio_logger.lifecycle import logger_lock


def test_explicit_missing_configuration_is_an_error(tmp_path):
    with pytest.raises(FileNotFoundError):
        load_config(tmp_path / "misspelled.yaml")


@pytest.mark.parametrize("data", [
    {"udp": {"prot": 2237}},
    {"udp": {"port": 65536}},
    {"receiver": {"timezone": "Asia/Singpore"}},
    {"receiver": {"locator": "ZZ99"}},
    {"dedupe": {"window_seconds": float("nan")}},
    {"paths": {"raw_dir": ""}},
])
def test_invalid_configuration_fails_at_load(data):
    with pytest.raises(ValidationError):
        AppConfig.model_validate(data)


def test_config_accepts_utf8_bom(tmp_path):
    path = tmp_path / "receiver.yaml"
    path.write_text("receiver:\n  name: 日本 receiver\n", encoding="utf-8-sig")
    assert load_config(path).receiver.name == "日本 receiver"


@pytest.mark.parametrize("section", ["udp", "http"])
def test_loaded_configuration_rejects_ephemeral_ports(tmp_path, section):
    path = tmp_path / "receiver.yaml"
    path.write_text(f"{section}:\n  port: 0\n", encoding="utf-8")
    with pytest.raises(ValueError, match=rf"Configured {section}\.port"):
        load_config(path)


def test_close_forgets_dial_before_instance_restarts(ingestor):
    ingestor.handle_datagram(encode_status(instance_id="A", dial_frequency_hz=14_074_000))
    ingestor.handle_datagram(encode_header(MessageTypeId.CLOSE, "A").dumps())
    row = ingestor.handle_datagram(encode_decode("CQ JA1XYZ PM95", instance_id="A"))
    assert row is not None
    assert row.dial_frequency_hz is None


def test_bad_packet_does_not_claim_live_receiver(ingestor, runtime):
    ingestor.handle_datagram(b"unrelated traffic")
    assert runtime.last_udp_at is None


def test_database_failure_is_exposed_and_health_returns_unavailable(
    ingestor, config, session_factory, runtime, monkeypatch
):
    def fail(*args, **kwargs):
        raise OSError("disk is full")

    monkeypatch.setattr(Repository, "insert_observation", fail)
    assert ingestor.handle_datagram(encode_decode("CQ JA1XYZ PM95")) is None
    response = TestClient(create_app(config, session_factory, runtime)).get("/health")
    assert response.status_code == 503
    assert response.json()["storage_error"]


def test_status_database_error_returns_structured_failure(config, session_factory, runtime, monkeypatch):
    def fail(*args, **kwargs):
        raise OSError("database unavailable")

    monkeypatch.setattr(Repository, "latest_observations", fail)
    client = TestClient(create_app(config, session_factory, runtime), raise_server_exceptions=False)
    response = client.get("/health")
    assert response.status_code == 503
    assert response.json()["ok"] is False


def test_lock_blocks_other_data_folder_using_same_database(config, tmp_path):
    other = config.model_copy(deep=True)
    other.paths.data_dir = str(tmp_path / "other")
    with logger_lock(config):
        with pytest.raises(RuntimeError, match="already in use"):
            with logger_lock(other):
                pytest.fail("second writer acquired the lock")
    with logger_lock(other):
        pass


def test_sender_restart_forgets_old_frequency(ingestor):
    ingestor.handle_datagram(encode_status(instance_id="A"), ("127.0.0.1", 10000))
    row = ingestor.handle_datagram(
        encode_decode("CQ JA1XYZ PM95", instance_id="A"), ("127.0.0.1", 10001),
    )
    assert row.dial_frequency_hz is None


@pytest.mark.parametrize("flag", [{"is_new": False}, {"off_air": True}])
def test_playback_is_excluded_from_live_measurements(ingestor, runtime, flag):
    assert ingestor.handle_datagram(encode_decode("CQ JA1XYZ PM95", **flag)) is None
    assert runtime.ignored_decodes == 1
    assert runtime.decode_count_session == 0


def test_queue_delay_preserves_receive_date_and_duplicate_window(ingestor):
    received_at = datetime(2026, 9, 14, 23, 59, 59, tzinfo=timezone.utc)
    packet = encode_decode("CQ JA1XYZ PM95", time_ms=86_385_000)
    first = ingestor.handle_datagram(packet, received_at=received_at, received_monotonic=1.0)
    second = ingestor.handle_datagram(packet, received_at=received_at, received_monotonic=4.0)
    assert first.timestamp_utc == datetime(2026, 9, 14, 23, 59, 45, tzinfo=timezone.utc)
    assert second is not None


def test_storage_health_recovers_after_success(ingestor, config, session_factory, runtime, monkeypatch):
    original = Repository.insert_observation
    monkeypatch.setattr(Repository, "insert_observation", lambda *a, **kw: (_ for _ in ()).throw(OSError("full")))
    packet = encode_decode("CQ JA1XYZ PM95")
    assert ingestor.handle_datagram(packet) is None
    monkeypatch.setattr(Repository, "insert_observation", original)
    assert ingestor.handle_datagram(packet) is not None
    payload = TestClient(create_app(config, session_factory, runtime)).get("/health")
    assert payload.status_code == 200
    assert payload.json()["storage_error"] is None
    assert payload.json()["storage_failures"] == 1


def test_failed_export_preserves_previous_file(config, session_factory, tmp_path, monkeypatch):
    from radio_logger.cli import export_cmd

    def fail(*args, **kwargs):
        yield "header\n"
        raise OSError("disk full")

    monkeypatch.setattr("radio_logger.cli._cfg", lambda _path: config)
    monkeypatch.setattr("radio_logger.service.init_database", lambda _config: session_factory)
    monkeypatch.setattr("radio_logger.export_csv.iter_observations_csv", fail)
    destination = tmp_path / "existing.csv"
    destination.write_text("previous export\n")
    with pytest.raises(OSError):
        export_cmd(output=destination, since=None, until=None, fmt="csv", config=None)
    assert destination.read_text() == "previous export\n"
    assert not list(tmp_path.glob("*.partial"))


@pytest.mark.parametrize("suffix", ["", "-wal", "-shm"])
def test_export_cannot_replace_database(config, monkeypatch, suffix):
    from pathlib import Path
    import typer
    from radio_logger.cli import export_cmd
    from radio_logger.database.backup import sqlite_path_from_url

    monkeypatch.setattr("radio_logger.cli._cfg", lambda _path: config)
    destination = Path(str(sqlite_path_from_url(config.database.url)) + suffix)
    with pytest.raises(typer.BadParameter):
        export_cmd(output=destination, since=None, until=None, fmt="csv", config=None)


@pytest.mark.parametrize("command", ["simulate", "seed", "replay"])
def test_cli_writers_respect_live_database_lock(config, tmp_path, monkeypatch, command):
    from radio_logger import cli

    monkeypatch.setattr("radio_logger.cli._cfg", lambda _path: config)
    fixture = tmp_path / "empty.txt"
    fixture.write_text("")
    actions = {
        "simulate": lambda: cli.simulate(config=None, japan_spike=False, count=1),
        "seed": lambda: cli.seed(config=None, fixture=fixture),
        "replay": lambda: cli.replay(path=fixture, config=None, speed=0),
    }
    with logger_lock(config):
        with pytest.raises(RuntimeError, match="already in use"):
            actions[command]()


def test_stale_receiver_status_does_not_label_new_decode(ingestor):
    from datetime import timedelta

    now = datetime.now(tz=timezone.utc)
    ingestor.handle_datagram(encode_status(), received_at=now - timedelta(minutes=3))
    row = ingestor.handle_datagram(encode_decode("CQ JA1XYZ PM95"), received_at=now)
    assert row.dial_frequency_hz is None


def test_receiver_instance_tracking_is_bounded(ingestor, runtime):
    for number in range(100):
        packet = encode_status(instance_id=f"instance-{number}")
        ingestor.handle_datagram(packet, ("127.0.0.1", number))
    assert len(runtime.receiver_statuses) <= 64
    assert len(ingestor._sender_addresses) <= 64


def test_late_status_does_not_turn_a_retransmission_into_another_observation(ingestor, runtime):
    packet = encode_decode("CQ JA1XYZ PM95")
    assert ingestor.handle_datagram(packet, received_monotonic=1.0) is not None
    ingestor.handle_datagram(encode_status(), received_monotonic=1.1)
    assert ingestor.handle_datagram(packet, received_monotonic=1.2) is None
    assert runtime.decode_count_session == 1
    assert runtime.duplicate_suppressed == 1
