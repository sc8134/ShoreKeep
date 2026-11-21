from sqlalchemy import event
from sqlmodel import Session, SQLModel, create_engine

from .config import settings

settings.data_dir.mkdir(parents=True, exist_ok=True)
settings.media_dir.mkdir(parents=True, exist_ok=True)
settings.work_dir.mkdir(parents=True, exist_ok=True)

engine = create_engine(
    f"sqlite:///{settings.db_path}",
    connect_args={"check_same_thread": False},
)


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


def init_db() -> None:
    from . import models  # noqa: F401

    SQLModel.metadata.create_all(engine)
    _migrate_job_table()


def session() -> Session:
    return Session(engine)


def get_session():
    with Session(engine) as db_session:
        yield db_session
