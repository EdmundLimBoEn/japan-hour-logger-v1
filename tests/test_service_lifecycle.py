from __future__ import annotations

import asyncio
import threading

import pytest

from radio_logger.ingest import Ingestor
from radio_logger.service import run_server
from radio_logger.database.models import Observation, Session as ReceiverSession
from radio_logger.database.engine import make_engine, make_session_factory
from radio_logger.wsjtx.protocol import encode_decode
from sqlalchemy import func, select


@pytest.mark.asyncio
async def test_slow_storage_keeps_event_loop_responsive_and_drains_on_stop(config, monkeypatch):
    callbacks = {}
    started = threading.Event()
    release = threading.Event()
    received = []
    original = Ingestor.handle_datagram
    config.http.port = 0

    def slow_ingest(self, data, addr, **kwargs):
        started.set()
        assert release.wait(3), "HTTP/event loop did not release the blocked storage worker"
        received.append(data)
        return original(self, data, addr, **kwargs)

    class Transport:
        def close(self):
            callbacks["on_connection_lost"](None)

    async def listener(_config, callback, **kwargs):
        callbacks.update(kwargs, callback=callback)
        return Transport(), "127.0.0.1:2237"

    packets = [encode_decode(f"CQ JA{number}XYZ PM95") for number in range(1, 4)]

    class Server:
        def __init__(self, server_config):
            self.config = server_config

        async def serve(self, **kwargs):
            for packet in packets:
                callbacks["callback"](packet, ("127.0.0.1", 54321))
            assert await asyncio.to_thread(started.wait, 1)
            await asyncio.sleep(0)
            assert received == []
            release.set()

    monkeypatch.setattr(Ingestor, "handle_datagram", slow_ingest)
    monkeypatch.setattr("radio_logger.service.start_udp_listener", listener)
    monkeypatch.setattr("uvicorn.Server", Server)
    try:
        await run_server(config)
    finally:
        release.set()
    assert received == packets
    engine = make_engine(config.database.url)
    try:
        with make_session_factory(engine)() as session:
            assert session.scalar(select(func.count()).select_from(Observation)) == 3
            assert session.scalar(select(ReceiverSession.ended_at)) is not None
    finally:
        engine.dispose()


@pytest.mark.asyncio
async def test_listener_rebinds_after_unexpected_close(config, monkeypatch):
    callbacks = {}
    bound = []
    config.http.port = 0

    class Transport:
        def close(self):
            callbacks["on_connection_lost"](None)

    async def listener(_config, callback, **kwargs):
        callbacks.update(kwargs)
        bound.append(Transport())
        return bound[-1], "127.0.0.1:2237"

    class Server:
        def __init__(self, server_config):
            self.config = server_config

        async def serve(self, **kwargs):
            callbacks["on_connection_lost"](OSError("adapter reset"))
            async with asyncio.timeout(3):
                while len(bound) < 2:
                    await asyncio.sleep(0.05)

    monkeypatch.setattr("radio_logger.service.start_udp_listener", listener)
    monkeypatch.setattr("uvicorn.Server", Server)
    await run_server(config)
    assert len(bound) == 2
