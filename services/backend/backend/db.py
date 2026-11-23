from __future__ import annotations

from contextlib import contextmanager

from sqlalchemy import event, inspect, text
from sqlmodel import Session, SQLModel, create_engine

from .config import settings

settings.data_dir.mkdir(parents=True, exist_ok=True)
settings.media_dir.mkdir(parents=True, exist_ok=True)
settings.work_dir.mkdir(parents=True, exist_ok=True)


def _engine_kwargs(url: str) -> dict:
    if url.startswith("sqlite"):
        return {"connect_args": {"check_same_thread": False}}
    return {
        "pool_pre_ping": True,
        "pool_size": settings.db_pool_size,
        "max_overflow": settings.db_max_overflow,
        "pool_recycle": settings.db_pool_recycle,
    }


engine = create_engine(settings.sqlalchemy_url, **_engine_kwargs(settings.sqlalchemy_url))
IS_POSTGRES = engine.dialect.name == "postgresql"


if not IS_POSTGRES:

    @event.listens_for(engine, "connect")
    def _set_sqlite_pragmas(dbapi_connection, _record) -> None:
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA busy_timeout=5000")
        cursor.close()


# Columns added after Phase 1; create_all() does not alter existing tables.
_JOB_COLUMNS = {
    "user_id": "VARCHAR",
    "client_hash": "VARCHAR",
    "cookies_enc": "TEXT",
    "want_subtitles": "BOOLEAN DEFAULT 0",
    "subtitle_name": "VARCHAR",
    "subtitle_path": "TEXT",
    "subtitle_lang": "VARCHAR",
    "chapters": "TEXT",
    "playlist_index": "INTEGER",
    "playlist_count": "INTEGER",
    "error_code": "VARCHAR",
    "max_filesize_mb": "INTEGER",
}

_REQUIRED_TABLES = {
    "user",
    "authsession",
    "agentpair",
    "agentdevice",
    "desktopjob",
    "subscription",
    "billingevent",
    "job",
}


def _migrate_job_table() -> None:
    with engine.begin() as conn:
        existing = {row[1] for row in conn.exec_driver_sql("PRAGMA table_info(job)")}
        if not existing:
            return
        for name, ddl in _JOB_COLUMNS.items():
            if name not in existing:
                conn.exec_driver_sql(f'ALTER TABLE job ADD COLUMN "{name}" {ddl}')
        conn.exec_driver_sql("CREATE INDEX IF NOT EXISTS ix_job_user_id ON job (user_id)")
        conn.exec_driver_sql("CREATE INDEX IF NOT EXISTS ix_job_client_hash ON job (client_hash)")


def _check_schema() -> None:
    missing = _REQUIRED_TABLES - set(inspect(engine).get_table_names())
    if missing:
        raise RuntimeError(
            "Database schema is not initialized (missing tables: "
            + ", ".join(sorted(missing))
            + "). Run `alembic upgrade head` before starting the app, or set "
            "DB_AUTO_CREATE=true for a throwaway database."
        )


def init_db() -> None:
    from . import models  # noqa: F401

    if IS_POSTGRES:
        if settings.db_auto_create:
            SQLModel.metadata.create_all(engine)
        _check_schema()
        return
    SQLModel.metadata.create_all(engine)
    _migrate_job_table()


def session() -> Session:
    return Session(engine)


def get_session():
    with Session(engine) as db_session:
        yield db_session


@contextmanager
def quota_guard(session: Session, key: str):
    """Serialize the quota check and insert for one key on PostgreSQL.

    SQLite already serializes writers and the in-process limiter covers local
    use, so this only takes a transaction-scoped advisory lock on Postgres.
    """
    if IS_POSTGRES:
        session.execute(text("SELECT pg_advisory_xact_lock(hashtext(:key))"), {"key": key})
    try:
        yield
    except Exception:
        session.rollback()
        raise
