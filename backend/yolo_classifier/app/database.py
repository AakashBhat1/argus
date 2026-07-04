from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine, async_sessionmaker
from sqlalchemy.orm import DeclarativeBase
import logging
import os

from app.config import DB_PATH, get_settings

settings = get_settings()
logger = logging.getLogger(__name__)

DATABASE_URL = settings.DATABASE_URL

engine = None
AsyncSessionLocal = None


def _build_engine(database_url: str):
    return create_async_engine(
        database_url,
        echo=settings.DEBUG,
    )


def _build_session_factory(current_engine):
    return async_sessionmaker(
        current_engine,
        class_=AsyncSession,
        expire_on_commit=False,
    )


def _switch_database(database_url: str) -> None:
    global DATABASE_URL, engine, AsyncSessionLocal
    DATABASE_URL = database_url
    engine = _build_engine(DATABASE_URL)
    AsyncSessionLocal = _build_session_factory(engine)


_switch_database(DATABASE_URL)


def get_session_factory():
    if AsyncSessionLocal is None:
        raise RuntimeError("Database session factory is not initialized")
    return AsyncSessionLocal


class Base(DeclarativeBase):
    pass


async def get_db() -> AsyncSession:
    session_factory = get_session_factory()
    async with session_factory() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
        finally:
            await session.close()


def _fallback_allowed() -> bool:
    return os.getenv("ALLOW_DB_FALLBACK", "false").strip().lower() in {
        "1", "true", "yes", "on",
    }


async def init_db():
    global engine
    if engine is None:
        raise RuntimeError("Database engine is not initialized")

    try:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        return
    except Exception as exc:
        if DATABASE_URL.startswith("sqlite"):
            # Already on SQLite — nothing to fall back to.
            raise

        if not _fallback_allowed():
            raise RuntimeError(
                f"Primary database is unreachable ({exc}). Refusing to silently "
                "fall back to SQLite: data written there is lost on container "
                "restart. Fix DATABASE_URL or set ALLOW_DB_FALLBACK=true to "
                "explicitly permit the SQLite fallback."
            ) from exc

        logger.warning(
            "Primary database unreachable (%s). ALLOW_DB_FALLBACK=true — "
            "falling back to local SQLite at %s.",
            exc,
            DB_PATH,
        )
        _switch_database(f"sqlite+aiosqlite:///{DB_PATH}")

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
