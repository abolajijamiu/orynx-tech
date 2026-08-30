"""Engine and session management. Postgres in production, SQLite for tests."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from sqlalchemy import Engine, create_engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from orynx.config import get_settings


class Base(DeclarativeBase):
    pass


_engine: Engine | None = None
_Session: sessionmaker[Session] | None = None


def _ensure_sqlite_dir(url: str) -> None:
    """Create the parent directory of a SQLite file.

    SQLite will not create missing directories and reports only "unable to open
    database file", which is an unhelpful first experience for anyone whose
    ORYNX_DATABASE_URL points somewhere that does not exist yet.
    """
    if not url.startswith("sqlite"):
        return
    _, _, path_part = url.partition("///")
    path_part = path_part.split("?", 1)[0]
    if not path_part or path_part == ":memory:":
        return
    parent = Path(path_part).expanduser().parent
    if parent and not parent.exists():
        parent.mkdir(parents=True, exist_ok=True)


def get_engine(url: str | None = None, echo: bool = False) -> Engine:
    global _engine, _Session
    if url is not None:
        # An explicit URL always builds a fresh engine; tests rely on this.
        _ensure_sqlite_dir(url)
        return create_engine(url, echo=echo, future=True)
    if _engine is None:
        database_url = get_settings().database_url
        _ensure_sqlite_dir(database_url)
        _engine = create_engine(database_url, echo=echo, future=True)
        _Session = sessionmaker(bind=_engine, expire_on_commit=False)
    return _engine


def get_sessionmaker() -> sessionmaker[Session]:
    global _Session
    if _Session is None:
        get_engine()
    assert _Session is not None
    return _Session


@contextmanager
def session_scope() -> Iterator[Session]:
    session = get_sessionmaker()()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def init_db(engine: Engine | None = None) -> None:
    """Create tables. Fine for development; use Alembic once schemas ship."""
    from orynx.db import models  # noqa: F401  (register mappers)

    Base.metadata.create_all(engine or get_engine())
