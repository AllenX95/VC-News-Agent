from __future__ import annotations

from collections.abc import Generator

from sqlalchemy import create_engine, event
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from .config import DB_PATH, SQLITE_JOURNAL_MODE, ensure_directories


ensure_directories()


class Base(DeclarativeBase):
    pass


engine = create_engine(
    f"sqlite:///{DB_PATH}",
    connect_args={"check_same_thread": False, "timeout": 60},
    future=True,
)


@event.listens_for(engine, "connect")
def set_sqlite_pragmas(dbapi_connection, _connection_record) -> None:
    cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA foreign_keys=ON")
    # The product target is WAL. This Codex sandbox denies SQLite journal/WAL
    # rename/delete operations, so the default here is configurable and set to
    # OFF for local smoke tests. Set VC_NEWS_SQLITE_JOURNAL_MODE=WAL on a normal
    # machine to enforce the PRD's WAL mode.
    cursor.execute(f"PRAGMA journal_mode={SQLITE_JOURNAL_MODE}")
    cursor.fetchone()
    cursor.execute("PRAGMA busy_timeout=60000")
    cursor.close()


SessionLocal = sessionmaker(bind=engine, autocommit=False, autoflush=False, future=True)


def get_db() -> Generator[Session, None, None]:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def ensure_schema_migrations() -> None:
    with engine.begin() as connection:
        llm_config_columns = {row[1] for row in connection.exec_driver_sql("PRAGMA table_info(llm_configs)")}
        if "context_window_tokens" not in llm_config_columns:
            connection.exec_driver_sql(
                "ALTER TABLE llm_configs "
                "ADD COLUMN context_window_tokens INTEGER NOT NULL DEFAULT 1000000"
            )


def create_db() -> None:
    from . import models  # noqa: F401

    Base.metadata.create_all(bind=engine)
    ensure_schema_migrations()
