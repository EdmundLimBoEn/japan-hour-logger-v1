from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest

from radio_logger.config import AppConfig
from radio_logger.database.engine import create_schema, make_engine, make_session_factory
from radio_logger.enrichment.pipeline import Enricher
from radio_logger.ingest import Ingestor
from radio_logger.models import RuntimeState

FIXTURES = Path(__file__).parent / "fixtures"


def make_test_config(tmp_path: Path | None = None) -> AppConfig:
    cfg = AppConfig()
    cfg.receiver.id = "test-rx"
    cfg.receiver.name = "Test Receiver"
    cfg.receiver.locator = "OJ11"
    cfg.receiver.timezone = "Asia/Singapore"
    cfg.paths.jsonl_events = False
    if tmp_path is not None:
        cfg.database.url = f"sqlite:///{(tmp_path / 'radio.db').as_posix()}"
        cfg.paths.data_dir = str(tmp_path)
        cfg.paths.raw_dir = str(tmp_path / "raw")
        cfg.paths.exports_dir = str(tmp_path / "exports")
        cfg.paths.backups_dir = str(tmp_path / "backups")
        cfg.ensure_directories()
    else:
        cfg.database.url = "sqlite:///:memory:"
    return cfg


@pytest.fixture
def config(tmp_path: Path) -> AppConfig:
    return make_test_config(tmp_path)


@pytest.fixture
def session_factory(config: AppConfig):
    engine = make_engine(config.database.url)
    create_schema(engine)
    return make_session_factory(engine)


@pytest.fixture
def runtime() -> RuntimeState:
    return RuntimeState(started_at=datetime.now(tz=timezone.utc))


@pytest.fixture
def enricher(config: AppConfig) -> Enricher:
    return Enricher(config)


@pytest.fixture
def ingestor(config: AppConfig, session_factory, runtime: RuntimeState) -> Ingestor:
    return Ingestor(config, session_factory, runtime, enricher=Enricher(config))
