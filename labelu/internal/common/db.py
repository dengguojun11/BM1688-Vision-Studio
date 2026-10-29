from contextlib import contextmanager
from typing import Generator, Iterator

from sqlalchemy import create_engine, event
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import Session
from sqlalchemy.orm import sessionmaker
from labelu.internal.common.config import settings

engine = None
database_url = settings.DATABASE_URL

if settings.DATABASE_URL.startswith('mysql'):
    engine = create_engine(
        database_url,
        pool_pre_ping=True,
        pool_size=int(settings.DATABASE_POOL_SIZE),
        max_overflow=int(settings.DATABASE_MAX_OVERFLOW),
        pool_timeout=int(settings.DATABASE_POOL_TIMEOUT),
        pool_recycle=int(settings.DATABASE_POOL_RECYCLE),
    )
else:
    # connect_args is needed only for SQLite. It's not needed for other databases
    engine = create_engine(
        database_url,
        connect_args={"check_same_thread": False, "timeout": 30},
    )

    @event.listens_for(engine, "connect")
    def _sqlite_pragmas(dbapi_connection, _connection_record) -> None:
        """提升并发读性能、减少锁等待（默认 SQLite 文件库）。"""
        cur = dbapi_connection.cursor()
        cur.execute("PRAGMA journal_mode=WAL")
        cur.execute("PRAGMA synchronous=NORMAL")
        cur.execute("PRAGMA busy_timeout=30000")
        cur.close()

SessionLocal = sessionmaker(autoflush=False, bind=engine)

Base = declarative_base()

# create database tables
def init_tables() -> None:
    import labelu.internal.domain.models  # noqa: F401 - register ORM mappers
    from labelu.internal.common.schema_governance import apply_schema_comment_defaults
    from labelu.internal.common.phase_one_schema import create_phase_one_tables

    apply_schema_comment_defaults(Base.metadata, include_governance=True)
    create_phase_one_tables(engine, Base.metadata)


def begin_transaction(session: Session):
    """Begin a writable transaction after any SQLAlchemy 2.0 autobegin-only work."""
    if session.in_transaction():
        session.rollback()
    return session.begin()


def get_db() -> Generator:
    db = None
    try:
        db = SessionLocal()
        yield db
    finally:
        if db is not None:
            db.close()


@contextmanager
def session_scope() -> Iterator[Session]:
    """后台线程/长任务用：短生命周期 Session，用完立即归还连接池。"""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
