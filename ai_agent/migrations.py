from __future__ import annotations

from datetime import datetime

from sqlalchemy import inspect, text
from sqlalchemy.engine import Connection, Engine


V03_MIGRATION_ID = "20260803_v03_financing_watch_reports_v2"


def _table_exists(connection: Connection, table_name: str) -> bool:
    return inspect(connection).has_table(table_name)


def _add_column_if_missing(connection: Connection, table_name: str, column_name: str, definition: str) -> None:
    if not _table_exists(connection, table_name):
        return
    columns = {column["name"] for column in inspect(connection).get_columns(table_name)}
    if column_name not in columns:
        connection.exec_driver_sql(f"ALTER TABLE {table_name} ADD COLUMN {column_name} {definition}")


def _apply_legacy_columns(connection: Connection) -> None:
    _add_column_if_missing(connection, "llm_configs", "context_window_tokens", "INTEGER NOT NULL DEFAULT 1000000")
    for column_name, definition in {
        "relevance_score": "INTEGER",
        "relevance_confidence": "FLOAT",
        "relevance_reasons_json": "TEXT",
        "review_status": "TEXT NOT NULL DEFAULT 'unread'",
        "review_note": "TEXT",
        "reviewed_at": "DATETIME",
    }.items():
        _add_column_if_missing(connection, "content_items", column_name, definition)


def _apply_v03_tables(connection: Connection) -> None:
    # The model metadata is the single schema definition. This explicit migration
    # entry point makes upgrades deterministic while still allowing fresh installs
    # to bootstrap through SQLAlchemy's normal create_all path.
    from .database import Base
    from . import models  # noqa: F401

    for table_name in (
        "financing_events",
        "event_contents",
        "event_change_logs",
        "watch_items",
        "reports",
        "report_inputs",
        "report_versions",
        "report_exports",
    ):
        table = Base.metadata.tables[table_name]
        table.create(connection, checkfirst=True)
    connection.exec_driver_sql(
        "CREATE UNIQUE INDEX IF NOT EXISTS uq_event_contents_content_id ON event_contents (content_id)"
    )


def _ensure_migration_table(connection: Connection) -> None:
    connection.exec_driver_sql(
        """
        CREATE TABLE IF NOT EXISTS schema_migrations (
            migration_id TEXT PRIMARY KEY,
            applied_at DATETIME NOT NULL
        )
        """
    )


def migration_pending(engine: Engine) -> bool:
    if not engine.url.database:
        return True
    with engine.connect() as connection:
        if not _table_exists(connection, "schema_migrations"):
            return True
        applied = connection.scalar(
            text("SELECT 1 FROM schema_migrations WHERE migration_id = :migration_id"),
            {"migration_id": V03_MIGRATION_ID},
        )
        return applied is None


def apply_migrations(engine: Engine) -> list[str]:
    applied_now: list[str] = []
    with engine.begin() as connection:
        _ensure_migration_table(connection)
        existing = connection.scalar(
            text("SELECT 1 FROM schema_migrations WHERE migration_id = :migration_id"),
            {"migration_id": V03_MIGRATION_ID},
        )
        if existing is not None:
            return applied_now

        _apply_legacy_columns(connection)
        _apply_v03_tables(connection)
        connection.execute(
            text("INSERT INTO schema_migrations (migration_id, applied_at) VALUES (:migration_id, :applied_at)"),
            {"migration_id": V03_MIGRATION_ID, "applied_at": datetime.utcnow()},
        )
        applied_now.append(V03_MIGRATION_ID)
    return applied_now
