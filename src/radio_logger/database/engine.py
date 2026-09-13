from __future__ import annotations

from collections.abc import Generator
from contextlib import contextmanager
from pathlib import Path

from sqlalchemy import create_engine, event, text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from radio_logger.database.models import Base


def make_engine(url: str, *, echo: bool = False) -> Engine:
    connect_args = {}
    if url.startswith("sqlite"):
        connect_args["check_same_thread"] = False
    engine = create_engine(url, echo=echo, future=True, connect_args=connect_args)
    if url.startswith("sqlite"):
        @event.listens_for(engine, "connect")
        def _sqlite_pragmas(dbapi_connection, _connection_record):  # type: ignore[no-untyped-def]
            cursor = dbapi_connection.cursor()
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.execute("PRAGMA journal_mode=WAL")
            cursor.execute("PRAGMA synchronous=NORMAL")
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
    if not url.startswith("sqlite:///"):
        return None
    path = url.removeprefix("sqlite:///")
    if path == ":memory:":
        return None
    file = Path(path)
    return file.stat().st_size if file.exists() else 0


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
