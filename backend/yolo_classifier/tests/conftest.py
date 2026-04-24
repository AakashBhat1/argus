"""claude --resume a64fb31a-b875-4666-a44d-e9f701a5a1ab                                                                                                          
Shared pytest fixtures for the yolo_classifier test suite.

Provides:
- in-memory SQLite async engine + session factory
- dependency-override helpers (get_db, get_current_active_user)
- pre-built authenticated AsyncClient for each role
- stub InferenceWorkerPool (no real model required)
"""

from __future__ import annotations

import asyncio
import uuid
from typing import AsyncGenerator
from unittest.mock import AsyncMock, MagicMock

import sys, os

# Set safe env vars BEFORE importing app modules so auth.py validation passes
os.environ.setdefault("SECRET_KEY", "test-secret-key-that-is-at-least-32-characters-long!")
os.environ.setdefault("DEBUG", "true")

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.database import Base
from app.models import Camera, User, UserRole
from app.services.auth import get_password_hash


@pytest.fixture(scope="session")
def event_loop():
    loop = asyncio.new_event_loop()
    yield loop
    loop.close()


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
    return async_sessionmaker(
        test_engine,
        class_=AsyncSession,
        expire_on_commit=False,
    )


@pytest_asyncio.fixture()
async def db_session(test_session_factory) -> AsyncGenerator[AsyncSession, None]:
    """Per-test session. Commits data so it is visible to other sessions
    (e.g. the override_get_db sessions used by app_with_db)."""
    async with test_session_factory() as session:
        yield session
        await session.commit()


@pytest_asyncio.fixture()
async def admin_user(db_session: AsyncSession) -> User:
    user = User(
        id=str(uuid.uuid4()),
        username=f"admin_{uuid.uuid4().hex[:6]}",
        hashed_password=get_password_hash("adminpass"),
        role=UserRole.ADMIN.value,
        tenant_id="tenant-1",
        is_active=True,
    )
    db_session.add(user)
    await db_session.flush()
    return user


@pytest_asyncio.fixture()
async def operator_user(db_session: AsyncSession) -> User:
    user = User(
        id=str(uuid.uuid4()),
        username=f"operator_{uuid.uuid4().hex[:6]}",
        hashed_password=get_password_hash("operatorpass"),
        role=UserRole.OPERATOR.value,
        tenant_id="tenant-1",
        is_active=True,
    )
    db_session.add(user)
    await db_session.flush()
    return user


@pytest_asyncio.fixture()
async def tenant2_user(db_session: AsyncSession) -> User:
    user = User(
        id=str(uuid.uuid4()),
        username=f"t2op_{uuid.uuid4().hex[:6]}",
        hashed_password=get_password_hash("t2pass"),
        role=UserRole.OPERATOR.value,
        tenant_id="tenant-2",
        is_active=True,
    )
    db_session.add(user)
    await db_session.flush()
    return user


@pytest_asyncio.fixture()
async def sample_camera(db_session: AsyncSession) -> Camera:
    camera = Camera(
        id=str(uuid.uuid4()),
        name="Test Camera",
        location="Lobby",
        stream_url="rtsp://192.168.1.10/live",
        tenant_id="tenant-1",
    )
    db_session.add(camera)
    await db_session.flush()
    return camera


@pytest.fixture()
def mock_inference_pool():
    pool = MagicMock()
    pool.is_running = True
    pool.submit = AsyncMock(return_value=[])
    pool.get_metrics = MagicMock(
        return_value={
            "is_running": True,
            "queue_depth": 0,
            "frames_processed": 0,
            "frames_dropped": 0,
            "batches_processed": 0,
            "avg_batch_latency_ms": 10.0,
        }
    )
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
    async with AsyncClient(
        transport=ASGITransport(app=app_with_db),
        base_url="http://test",
    ) as client:
        yield client


@pytest_asyncio.fixture()
async def auth_client(app_with_db, admin_user) -> AsyncGenerator[AsyncClient, None]:
    from app.services.auth import create_access_token
    from datetime import timedelta

    token = create_access_token(
        data={
            "sub": admin_user.username,
            "role": admin_user.role,
            "tenant_id": admin_user.tenant_id,
        },
        expires_delta=timedelta(minutes=30),
    )
    async with AsyncClient(
        transport=ASGITransport(app=app_with_db),
        base_url="http://test",
        headers={"Authorization": f"Bearer {token}"},
    ) as client:
        yield client
