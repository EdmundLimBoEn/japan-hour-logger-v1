from __future__ import annotations

import logging
import time
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy.orm import Session, sessionmaker

from radio_logger.api.app import create_app
from radio_logger.config import AppConfig
from radio_logger.database.engine import create_schema, make_engine, make_session_factory
from radio_logger.ingest import Ingestor, preserve_all_txt_copy
from radio_logger.models import RuntimeState
from radio_logger.wsjtx.all_txt import iter_all_txt_lines, parse_jsonl_line
from radio_logger.wsjtx.listener import start_udp_listener

log = logging.getLogger(__name__)


def build_runtime() -> RuntimeState:
    return RuntimeState(started_at=datetime.now(tz=timezone.utc))


def init_database(config: AppConfig) -> sessionmaker[Session]:
    config.ensure_directories()
    engine = make_engine(config.database.url)
    create_schema(engine)
    return make_session_factory(engine)


def build_ingestor(config: AppConfig, runtime: RuntimeState | None = None) -> tuple[Ingestor, RuntimeState, sessionmaker[Session]]:
    runtime = runtime or build_runtime()
    factory = init_database(config)
    return Ingestor(config, factory, runtime), runtime, factory


async def run_server(
    config: AppConfig,
    *,
    listen_udp: bool = True,
    host: str | None = None,
    port: int | None = None,
) -> None:
    import uvicorn

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    ingestor, runtime, _factory = build_ingestor(config)
    app = create_app(config, ingestor.session_factory, runtime)
    transport = None
    if listen_udp:
        def _on_udp(data: bytes, addr: tuple[str, int]) -> None:
            ingestor.handle_datagram(data, addr)

        transport, bound = await start_udp_listener(config.udp, _on_udp)
        runtime.udp_bound = bound
    server = uvicorn.Server(
        uvicorn.Config(
            app,
            host=host or config.http.host,
            port=port or config.http.port,
            log_level="info",
        )
    )
    try:
        await server.serve()
    finally:
        if transport is not None:
            transport.close()


def replay_file(
    ingestor: Ingestor,
    path: Path,
    *,
    speed: float = 0.0,
    preserve_copy: bool = True,
) -> int:
    if preserve_copy and path.suffix.lower() in {".txt", ".log"}:
        try:
            preserve_all_txt_copy(path, Path(ingestor.config.paths.raw_dir))
        except OSError as exc:
            log.warning("could not preserve replay source: %s", exc)
    stored = 0
    prev = None
    for raw in _iter_replay(path):
        if speed > 0 and prev is not None:
            delta = (raw.decode_time_utc - prev.decode_time_utc).total_seconds()
            if delta > 0:
                time.sleep(delta / speed)
        if ingestor.ingest_raw(raw) is not None:
            stored += 1
        prev = raw
    return stored


def _iter_replay(path: Path):
    with path.open(errors="replace") as lines:
        if path.suffix.lower() in {".jsonl", ".json"}:
            for line in lines:
                parsed = parse_jsonl_line(line)
                if parsed is not None:
                    yield parsed
            return
        yield from iter_all_txt_lines(lines)
