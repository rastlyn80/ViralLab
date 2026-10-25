"""Shared FastAPI dependencies.

Extracted so the worker API (`app.py`) and the UI API (`ui.py`) can both
take a database session without importing each other. `app.py` owns the
lifespan and calls `configure()` once at startup; everything else just
depends on `get_session`.
"""

from __future__ import annotations

from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

_engine = None
_Session: sessionmaker | None = None


def configure(db_url: str) -> None:
    """Bind the process to a database. Called once, from the app lifespan.

    Rebinding is allowed because tests point the app at a disposable
    database at runtime.
    """
    global _engine, _Session
    _engine = create_engine(db_url, future=True, pool_pre_ping=True)
    _Session = sessionmaker(_engine, expire_on_commit=False, future=True)


def engine():
    return _engine


def session_factory() -> sessionmaker:
    if _Session is None:
        raise RuntimeError("database not configured")
    return _Session


def dispose() -> None:
    global _engine, _Session
    if _engine is not None:
        _engine.dispose()
    _engine, _Session = None, None


def get_session() -> Session:
    if _Session is None:
        raise HTTPException(503, "database not initialised")
    with _Session() as session:
        yield session
