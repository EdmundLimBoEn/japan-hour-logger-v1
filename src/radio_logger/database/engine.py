from __future__ import annotations

from collections.abc import Generator
from contextlib import contextmanager
from pathlib import Path

from sqlalchemy import create_engine, event, text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from radio_logger.database.backup import sqlite_path_from_url
from radio_logger.database.models import Base


def make_engine(url: str, *, echo: bool = False) -> Engine:
    connect_args = {}
    engine_args = {}
    if url.startswith("sqlite"):
        connect_args["check_same_thread"] = False
        connect_args["timeout"] = 5.0
        if sqlite_path_from_url(url) is None:
            engine_args["poolclass"] = StaticPool
    engine = create_engine(url, echo=echo, future=True, connect_args=connect_args, **engine_args)
    if url.startswith("sqlite"):
        @event.listens_for(engine, "connect")
        def _sqlite_pragmas(dbapi_connection, _connection_record):  # type: ignore[no-untyped-def]
            cursor = dbapi_connection.cursor()
            try:
                cursor.execute("PRAGMA foreign_keys=ON")
                cursor.execute("PRAGMA journal_mode=WAL")
                cursor.execute("PRAGMA synchronous=FULL")
            finally:
                cursor.close()
    return engine


def make_session_factory(engine: Engine) -> sessionmaker[Session]:
    return sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)


def create_schema(engine: Engine) -> None:
    Base.metadata.create_all(engine)


@contextmanager
def session_scope(factory: sessionmaker[Session]) -> Generator[Session, None, None]:
    session = factory()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def database_size_bytes(url: str) -> int | None:
    file = sqlite_path_from_url(url)
    if file is None:
        return None
    size = 0
    for path in (file, Path(f"{file}-wal"), Path(f"{file}-shm")):
        try:
            size += path.stat().st_size
        except FileNotFoundError:
            pass
    return size


def db_writable(engine: Engine) -> bool:
    try:
        with engine.connect() as conn:
            if engine.url.drivername.startswith("sqlite"):
                transaction = conn.begin()
                try:
                    conn.execute(text("UPDATE observations SET id = id WHERE 0"))
                finally:
                    transaction.rollback()
            else:
                conn.execute(text("SELECT 1"))
        return True
    except Exception:
        return False
