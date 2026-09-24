"""Shared fixtures for the parking service tests.

Hermetic: dotenv loading is disabled and every setting-named environment
variable is cleared before ``app`` is imported.
"""

from __future__ import annotations

import os
import sys
from typing import AsyncGenerator
from unittest.mock import AsyncMock, MagicMock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.dirname(__file__))

_PINNED_ENV = {"ARGUS_ENV_FILE": "", "DEBUG": "true"}


def _isolate_environment() -> None:
    from app.config import Settings

    for name in Settings.model_fields:
        if name not in _PINNED_ENV:
            os.environ.pop(name, None)
    os.environ.update(_PINNED_ENV)


_isolate_environment()

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.database import Base
import app.models  # noqa: F401  (register every table before create_all)
from app.services import auth as auth_module
from support import identity
from support.identity import User, UserRole


@pytest.fixture(autouse=True, scope="session")
def _trust_test_identity_provider():
    auth_module.set_token_verifier(identity.verifier())
    yield
    auth_module.set_token_verifier(None)


TEST_DB_URL = "sqlite+aiosqlite:///:memory:"


@pytest_asyncio.fixture(scope="session")
async def test_engine():
    engine = create_async_engine(TEST_DB_URL, echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield engine
    await engine.dispose()


@pytest_asyncio.fixture(scope="session")
async def test_session_factory(test_engine):
    return async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)


@pytest_asyncio.fixture()
async def db_session(test_session_factory) -> AsyncGenerator[AsyncSession, None]:
    async with test_session_factory() as session:
        yield session
        await session.commit()


@pytest.fixture()
def admin_user() -> User:
    return User(username="admin_test", role=UserRole.ADMIN.value, tenant_id="tenant-1")


@pytest.fixture()
def operator_user() -> User:
    return User(username="operator_test", role=UserRole.OPERATOR.value, tenant_id="tenant-1")


@pytest.fixture()
def tenant2_user() -> User:
    return User(username="t2_operator", role=UserRole.OPERATOR.value, tenant_id="tenant-2")


@pytest.fixture()
def mock_inference_pool():
    pool = MagicMock()
    pool.is_running = True
    pool.submit = AsyncMock(return_value=[])
    pool.get_metrics = MagicMock(return_value={"is_running": True, "avg_batch_latency_ms": 10.0})
    return pool


@pytest_asyncio.fixture()
async def app_with_db(test_session_factory):
    from app.main import app
    from app import database as db_module
    from app.database import get_db

    original_factory = db_module.AsyncSessionLocal
    db_module.AsyncSessionLocal = test_session_factory

    async def override_get_db():
        async with test_session_factory() as session:
            try:
                yield session
                await session.commit()
            except Exception:
                await session.rollback()
                raise

    app.dependency_overrides[get_db] = override_get_db
    yield app
    app.dependency_overrides.clear()
    db_module.AsyncSessionLocal = original_factory


@pytest_asyncio.fixture()
async def anon_client(app_with_db) -> AsyncGenerator[AsyncClient, None]:
    async with AsyncClient(transport=ASGITransport(app=app_with_db), base_url="http://test") as client:
        yield client


@pytest_asyncio.fixture()
async def auth_client(app_with_db, admin_user) -> AsyncGenerator[AsyncClient, None]:
    async with AsyncClient(
        transport=ASGITransport(app=app_with_db),
        base_url="http://test",
        headers={"Authorization": f"Bearer {identity.token_for(admin_user)}"},
    ) as client:
        yield client


@pytest.fixture
def sample_video(tmp_path, monkeypatch) -> str:
    """A synthetic lot clip exposed as ``video://sample-lot.mp4``."""
    from support.synthetic_media import sample_clip

    from argus_vision import sources as _sources

    video_dir = tmp_path / "video"
    sample_clip(video_dir / "sample-lot.mp4")
    monkeypatch.setattr(_sources, "video_source_roots", lambda: [video_dir])
    return "video://sample-lot.mp4"
