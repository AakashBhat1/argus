"""First-administrator bootstrap: nobody racing a fresh deployment gets admin."""

from __future__ import annotations

import pytest
from sqlalchemy import delete, select

from app.cli.create_admin import AdminCreationError, create_admin
from app.config import get_settings
from app.models import User

TOKEN = "b" * 40
BODY = {"username": "first-admin", "password": "a-long-enough-password"}


@pytest.fixture
async def empty_users(db_session):
    await db_session.execute(delete(User))
    await db_session.commit()
    yield


@pytest.fixture
def production(monkeypatch):
    monkeypatch.setenv("DEBUG", "false")
    monkeypatch.setenv("AUTH_BOOTSTRAP_TOKEN", TOKEN)
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


async def test_production_refuses_anonymous_bootstrap(empty_users, production, anon_client):
    assert (await anon_client.post("/api/v1/auth/users", json=BODY)).status_code == 403
    wrong = {"X-Argus-Bootstrap-Token": "c" * 40}
    assert (await anon_client.post("/api/v1/auth/users", json=BODY, headers=wrong)).status_code == 403
    ok = await anon_client.post("/api/v1/auth/users", json=BODY, headers={"X-Argus-Bootstrap-Token": TOKEN})
    assert ok.status_code == 200 and ok.json()["role"] == "admin"
    # Once a user exists the token is worthless.
    again = await anon_client.post("/api/v1/auth/users", json={**BODY, "username": "second"}, headers={"X-Argus-Bootstrap-Token": TOKEN})
    assert again.status_code == 403


async def test_short_bootstrap_tokens_are_ignored(empty_users, monkeypatch, anon_client):
    monkeypatch.setenv("DEBUG", "false")
    monkeypatch.setenv("AUTH_BOOTSTRAP_TOKEN", "short")
    get_settings.cache_clear()
    try:
        response = await anon_client.post("/api/v1/auth/users", json=BODY, headers={"X-Argus-Bootstrap-Token": "short"})
        assert response.status_code == 403
    finally:
        get_settings.cache_clear()


async def test_password_policy(empty_users, anon_client):
    for password in ("short", "x" * 73, "é" * 40):
        response = await anon_client.post("/api/v1/auth/users", json={**BODY, "password": password})
        assert response.status_code == 422, password
    assert (await anon_client.post("/api/v1/auth/users", json={**BODY, "username": "bad name!"})).status_code == 422


async def test_cli_creates_an_admin(empty_users, test_session_factory, db_session):
    user = await create_admin("shell-admin", "a-long-enough-password", session_factory=test_session_factory)
    assert user.role == "admin"
    stored = (await db_session.execute(select(User).where(User.username == "shell-admin"))).scalar_one()
    assert stored.hashed_password != "a-long-enough-password"
    with pytest.raises(AdminCreationError, match="exists"):
        await create_admin("shell-admin", "a-long-enough-password", session_factory=test_session_factory)
    with pytest.raises(AdminCreationError):
        await create_admin("other", "short", session_factory=test_session_factory)
