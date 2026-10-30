from contextlib import asynccontextmanager
from typing import AsyncGenerator
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from config.settings import settings
from database.models import Base
from utils.logger import logger

_engine: AsyncEngine | None = None
_sessionmaker: async_sessionmaker[AsyncSession] | None = None


def get_engine() -> AsyncEngine:
    global _engine
    if _engine is None:
        connect_args = {}
        if "sqlite" in settings.DATABASE_URL:
            connect_args["check_same_thread"] = False

        _engine = create_async_engine(
            settings.DATABASE_URL,
            echo=False,
            future=True,
            connect_args=connect_args,
        )
    return _engine


def get_sessionmaker() -> async_sessionmaker[AsyncSession]:
    global _sessionmaker
    if _sessionmaker is None:
        _sessionmaker = async_sessionmaker(
            bind=get_engine(),
            class_=AsyncSession,
            expire_on_commit=False,
            autoflush=False,
        )
    return _sessionmaker


@asynccontextmanager
async def get_session() -> AsyncGenerator[AsyncSession, None]:
    """Async context manager for database sessions."""
    sm = get_sessionmaker()
    async with sm() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise


async def init_db() -> None:
    """Initialize database tables."""
    engine = get_engine()
    logger.info("Initializing database schema...")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        # SQLite column migration check for dir_path
        try:
            from sqlalchemy import text
            res = await conn.execute(text("PRAGMA table_info(download_jobs)"))
            cols = [row[1] for row in res.fetchall()]
            if "dir_path" not in cols:
                logger.info("Migrating database: adding dir_path column to download_jobs table")
                await conn.execute(text("ALTER TABLE download_jobs ADD COLUMN dir_path TEXT DEFAULT '/'"))
        except Exception as e:
            logger.debug(f"Column check or migration passed: {e}")
    logger.info("Database schema initialized successfully.")
