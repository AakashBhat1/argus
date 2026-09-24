"""Security tests for the explicit admin-reset utility."""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from scripts import reset_admin


class _SessionContext:
    def __init__(self):
        self.session = AsyncMock()
        self.session.add = lambda user: setattr(self, "added_user", user)
        self.added_user = None

    async def __aenter__(self):
        return self.session

    async def __aexit__(self, *_args):
        return False


@pytest.mark.asyncio
async def test_reset_admin_requires_password_before_database_changes(
    monkeypatch, capsys
):
    monkeypatch.delenv("ARGUS_ADMIN_PASSWORD", raising=False)
    init_db = AsyncMock()
    monkeypatch.setattr(reset_admin, "init_db", init_db)

    assert await reset_admin.main() == 1

    init_db.assert_not_awaited()
    captured = capsys.readouterr()
    assert "ARGUS_ADMIN_PASSWORD must be set" in captured.err
    assert captured.out == ""


@pytest.mark.asyncio
async def test_reset_admin_hashes_environment_password_without_printing_it(
    monkeypatch, capsys
):
    password = "unique-test-password"
    monkeypatch.setenv("ARGUS_ADMIN_PASSWORD", password)
    monkeypatch.setattr(reset_admin, "init_db", AsyncMock())
    context = _SessionContext()
    monkeypatch.setattr(reset_admin, "get_session_factory", lambda: lambda: context)
    hashes = []
    monkeypatch.setattr(
        reset_admin,
        "get_password_hash",
        lambda raw_password: hashes.append(raw_password) or "hashed-password",
    )

    assert await reset_admin.main() == 0

    assert hashes == [password]
    context.session.execute.assert_awaited_once()
    context.session.commit.assert_awaited_once()
    assert context.added_user.username == "admin"
    assert context.added_user.hashed_password == "hashed-password"
    captured = capsys.readouterr()
    assert captured.err == ""
    assert captured.out.strip() == "Successfully reset admin user: admin"
    assert password not in captured.out
