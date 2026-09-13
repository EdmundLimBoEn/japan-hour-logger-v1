from __future__ import annotations

import asyncio
import logging
import time
from datetime import datetime, timezone
from contextlib import contextmanager
from pathlib import Path

from sqlalchemy.orm import Session, sessionmaker

from radio_logger.api.app import create_app
from radio_logger.config import AppConfig
from radio_logger.database.engine import create_schema, make_engine, make_session_factory
from radio_logger.ingest import Ingestor, preserve_all_txt_copy
from radio_logger.models import RuntimeState
from radio_logger.lifecycle import logger_lock
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


@contextmanager
def ingestor_session(config: AppConfig):
    with logger_lock(config):
        ingestor, _runtime, factory = build_ingestor(config)
        try:
            yield ingestor
        finally:
            ingestor.close()
            factory.kw["bind"].dispose()


async def run_server(
    config: AppConfig,
    *,
    listen_udp: bool = True,
    host: str | None = None,
    port: int | None = None,
) -> None:
    with logger_lock(config):
        await _serve(config, listen_udp=listen_udp, host=host, port=port)


async def _serve(config: AppConfig, *, listen_udp: bool, host: str | None, port: int | None) -> None:
    import uvicorn

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    ingestor, runtime, factory = build_ingestor(config)
    app = create_app(config, factory, runtime)
    queue: asyncio.Queue[tuple[bytes, tuple[str, int], datetime, float] | None] = asyncio.Queue(maxsize=1024)
    transport = None
    worker = None
    monitor = None
    http_socket = None
    lost = asyncio.Event()

    def on_udp(data: bytes, addr: tuple[str, int]) -> None:
        try:
            queue.put_nowait((data, addr, datetime.now(tz=timezone.utc), time.monotonic()))
            runtime.queue_depth = queue.qsize()
        except asyncio.QueueFull:
            runtime.dropped_datagrams += 1
            runtime.last_error = "UDP queue full. Some packets were lost. Preserve WSJT-X ALL.TXT."
            if runtime.dropped_datagrams == 1 or runtime.dropped_datagrams % 100 == 0:
                log.error(runtime.last_error)

    def on_error(exc: Exception) -> None:
        runtime.udp_error = str(exc)
        runtime.last_error = f"UDP socket: {exc}"
        log.warning("UDP socket error: %s", exc)

    def on_lost(exc: Exception | None) -> None:
        runtime.udp_bound = None
        runtime.udp_error = str(exc) if exc else "UDP listener closed; reconnecting"
        lost.set()

    async def consume() -> None:
        while True:
            item = await queue.get()
            runtime.queue_depth = queue.qsize()
            try:
                if item is None:
                    return
                data, addr, received_at, received_monotonic = item
                await asyncio.to_thread(
                    ingestor.handle_datagram, data, addr,
                    received_at=received_at, received_monotonic=received_monotonic,
                )
            except Exception as exc:
                runtime.dropped_datagrams += 1
                runtime.last_error = f"Packet processing failed: {exc}"
                log.exception("Packet processing failed; receiver continues")
            finally:
                queue.task_done()

    async def bind_udp() -> None:
        nonlocal transport
        lost.clear()
        transport, bound = await start_udp_listener(
            config.udp, on_udp, on_error=on_error, on_connection_lost=on_lost,
        )
        runtime.udp_bound = bound
        runtime.udp_error = None

    async def reconnect() -> None:
        while True:
            await lost.wait()
            await asyncio.sleep(1)
            try:
                await bind_udp()
            except OSError as exc:
                on_error(exc)
                lost.set()

    server = uvicorn.Server(uvicorn.Config(
        app,
        host=host if host is not None else config.http.host,
        port=port if port is not None else config.http.port,
        log_level="info",
    ))
    try:
        http_socket = server.config.bind_socket()
        if listen_udp:
            await bind_udp()
            worker = asyncio.create_task(consume())
            monitor = asyncio.create_task(reconnect())
        await server.serve(sockets=[http_socket])
    finally:
        if monitor is not None:
            monitor.cancel()
            await asyncio.gather(monitor, return_exceptions=True)
        if transport is not None:
            transport.close()
        if worker is not None:
            if queue.qsize():
                log.info("Saving %s queued packets before shutdown", queue.qsize())
            await queue.join()
            await queue.put(None)
            await worker
        await asyncio.to_thread(ingestor.close)
        factory.kw["bind"].dispose()
        if http_socket is not None:
            http_socket.close()


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
    with path.open(encoding="utf-8-sig", errors="replace") as lines:
        if path.suffix.lower() in {".jsonl", ".json"}:
            for number, line in enumerate(lines, start=1):
                try:
                    parsed = parse_jsonl_line(line)
                except (ValueError, TypeError, OverflowError) as exc:
                    log.warning("Skipping invalid JSONL record %s:%s: %s", path, number, exc)
                    continue
                if parsed is not None:
                    yield parsed
            return
        yield from iter_all_txt_lines(lines)
