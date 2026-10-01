"""
api/db/database.py — engine, session factory, and the request-scoped
session dependency.

The DATABASE_URL comes from the environment (api/core/config.py):
PostgreSQL via psycopg in production, SQLite as the local-dev fallback.
All state changes and their Digital Twin events share ONE session so each
request commits atomically (see api/services/*).
"""

from __future__ import annotations

from collections.abc import Generator

from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from api.core.config import get_settings

settings = get_settings()

engine = create_engine(
    settings.database_url,
    connect_args={"check_same_thread": False} if settings.is_sqlite else {},
    pool_pre_ping=not settings.is_sqlite,
)

SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


class Base(DeclarativeBase):
    pass


def get_db() -> Generator[Session, None, None]:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
