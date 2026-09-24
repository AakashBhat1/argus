"""Dashboard cookie sessions: flags, CSRF, rotation, reuse detection, logout."""

from __future__ import annotations

from datetime import timedelta

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select, update

from app.models import AuthSession, User
from app.routers import auth as auth_router
from app.services.login_attempts import LoginAttemptLimiter
from app.utils import utc_now
from argus_common.web_auth import ACCESS_COOKIE, CSRF_HEADER, REFRESH_COOKIE, WS_AUTH_CLOSE_CODE

ORIGIN = "https://argus.test"
CSRF = {CSRF_HEADER: "1", "origin": ORIGIN}


@pytest.fixture(autouse=True)
def fresh_login_limiter(monkeypatch):
    monkeypatch.setattr(auth_router, "login_attempt_limiter", LoginAttemptLimiter())


@pytest_asyncio.fixture()
async def browser(app_with_db):
    # https so the client stores and returns Secure cookies.
    async with AsyncClient(transport=ASGITransport(app=app_with_db), base_url=ORIGIN) as client:
        yield client


async def _sign_in(browser, user, password="adminpass"):
    return await browser.post(
        "/api/v1/auth/session", data={"username": user.username, "password": password}, headers=CSRF
    )


def _set_cookie_headers(response) -> dict[str, str]:
    headers = {}
    for value in response.headers.get_list("set-cookie"):
        headers[value.split("=", 1)[0]] = value.lower()
    return headers


async def test_sign_in_sets_hardened_cookies(browser, admin_user, db_session):
    await db_session.commit()
    response = await _sign_in(browser, admin_user)
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["username"] == admin_user.username and "access_token" not in body

    cookies = _set_cookie_headers(response)
    access, refresh = cookies[ACCESS_COOKIE], cookies[REFRESH_COOKIE]
    for flags in (access, refresh):
        assert "httponly" in flags and "secure" in flags and "samesite=strict" in flags
        assert "domain=" not in flags
    assert "path=/;" in access or access.endswith("path=/")
    assert "path=/api/v1/auth" in refresh

    me = await browser.get("/api/v1/auth/users/me")
    assert me.status_code == 200 and me.json()["username"] == admin_user.username


async def test_sign_in_requires_the_csrf_header_and_our_origin(browser, admin_user, db_session):
    await db_session.commit()
    data = {"username": admin_user.username, "password": "adminpass"}
    assert (await browser.post("/api/v1/auth/session", data=data)).status_code == 403
    foreign = {CSRF_HEADER: "1", "origin": "https://evil.example"}
    assert (await browser.post("/api/v1/auth/session", data=data, headers=foreign)).status_code == 403


async def test_wrong_password_and_inactive_users_get_no_session(browser, admin_user, db_session):
    await db_session.commit()
    assert (await _sign_in(browser, admin_user, password="wrong")).status_code == 401
    admin_user.is_active = False
    await db_session.commit()
    assert (await _sign_in(browser, admin_user)).status_code == 401


async def test_cookie_authenticated_writes_need_csrf(browser, admin_user, db_session):
    await db_session.commit()
    await _sign_in(browser, admin_user)
    body = {"mode": "armed"}
    assert (await browser.put("/api/v1/security/arm", json=body)).status_code == 403
    evil = {CSRF_HEADER: "1", "origin": "https://evil.example"}
    assert (await browser.put("/api/v1/security/arm", json=body, headers=evil)).status_code == 403
    assert (await browser.put("/api/v1/security/arm", json=body, headers=CSRF)).status_code == 200


async def test_refresh_rotates_and_detects_reuse(browser, admin_user, db_session):
    await db_session.commit()
    await _sign_in(browser, admin_user)
    first = browser.cookies.get(REFRESH_COOKIE)

    refreshed = await browser.post("/api/v1/auth/refresh", headers=CSRF)
    assert refreshed.status_code == 200
    second = browser.cookies.get(REFRESH_COOKIE)
    assert second and second != first

    # A second tab racing with the old token: refused, session kept.
    browser.cookies.set(REFRESH_COOKIE, first, domain="argus.test", path="/api/v1/auth")
    assert (await browser.post("/api/v1/auth/refresh", headers=CSRF)).status_code == 401
    browser.cookies.clear()
    browser.cookies.set(REFRESH_COOKIE, second, domain="argus.test", path="/api/v1/auth")
    assert (await browser.post("/api/v1/auth/refresh", headers=CSRF)).status_code == 200
    third = browser.cookies.get(REFRESH_COOKIE)

    # The same old token long after rotation: a copy is in someone else's
    # hands, so the whole session dies.
    await db_session.execute(
        update(AuthSession).where(AuthSession.used_at.is_not(None)).values(used_at=utc_now() - timedelta(minutes=5))
    )
    await db_session.commit()
    browser.cookies.clear()
    browser.cookies.set(REFRESH_COOKIE, first, domain="argus.test", path="/api/v1/auth")
    assert (await browser.post("/api/v1/auth/refresh", headers=CSRF)).status_code == 401
    browser.cookies.clear()
    browser.cookies.set(REFRESH_COOKIE, third, domain="argus.test", path="/api/v1/auth")
    assert (await browser.post("/api/v1/auth/refresh", headers=CSRF)).status_code == 401


async def test_refresh_needs_csrf_and_a_session(browser, admin_user, db_session):
    await db_session.commit()
    assert (await browser.post("/api/v1/auth/refresh", headers=CSRF)).status_code == 401
    await _sign_in(browser, admin_user)
    assert (await browser.post("/api/v1/auth/refresh")).status_code == 403


async def test_only_hashes_are_stored(browser, admin_user, db_session):
    await db_session.commit()
    await _sign_in(browser, admin_user)
    token = browser.cookies.get(REFRESH_COOKIE)
    rows = (await db_session.execute(select(AuthSession).where(AuthSession.user_id == admin_user.id))).scalars().all()
    assert rows and all(token not in row.token_hash for row in rows)


async def test_logout_ends_the_session(browser, admin_user, db_session):
    await db_session.commit()
    await _sign_in(browser, admin_user)
    token = browser.cookies.get(REFRESH_COOKIE)
    response = await browser.post("/api/v1/auth/logout", headers=CSRF)
    assert response.status_code == 204
    assert ACCESS_COOKIE in _set_cookie_headers(response)
    browser.cookies.set(REFRESH_COOKIE, token, domain="argus.test", path="/api/v1/auth")
    assert (await browser.post("/api/v1/auth/refresh", headers=CSRF)).status_code == 401


async def test_deactivated_user_cannot_refresh(browser, admin_user, db_session):
    await db_session.commit()
    await _sign_in(browser, admin_user)
    user = await db_session.get(User, admin_user.id)
    user.is_active = False
    await db_session.commit()
    assert (await browser.post("/api/v1/auth/refresh", headers=CSRF)).status_code == 401


async def test_sessions_end_at_their_absolute_lifetime(browser, admin_user, db_session):
    await db_session.commit()
    await _sign_in(browser, admin_user)
    await db_session.execute(
        update(AuthSession)
        .where(AuthSession.user_id == admin_user.id)
        .values(family_expires_at=utc_now() - timedelta(seconds=1))
    )
    await db_session.commit()
    assert (await browser.post("/api/v1/auth/refresh", headers=CSRF)).status_code == 401


async def test_bearer_api_clients_are_unaffected(anon_client, admin_user, db_session):
    await db_session.commit()
    token = (
        await anon_client.post("/api/v1/auth/token", data={"username": admin_user.username, "password": "adminpass"})
    ).json()["access_token"]
    headers = {"Authorization": f"Bearer {token}"}
    # No CSRF header needed: browsers never attach Authorization on their own.
    response = await anon_client.put("/api/v1/security/arm", json={"mode": "disarmed"}, headers=headers)
    assert response.status_code == 200


async def test_websocket_accepts_the_session_cookie_from_our_origin_only(app_with_db, admin_user, db_session):
    from starlette.testclient import TestClient
    from starlette.websockets import WebSocketDisconnect

    from app.services.auth import create_access_token

    await db_session.commit()
    token = create_access_token(
        {"sub": admin_user.username, "uid": admin_user.id, "role": admin_user.role, "tenant_id": admin_user.tenant_id},
        expires_delta=timedelta(minutes=5),
    )
    client = TestClient(app_with_db)
    ours = {"cookie": f"{ACCESS_COOKIE}={token}", "origin": ORIGIN, "host": "argus.test"}
    with client.websocket_connect("/ws/global", headers=ours) as ws:
        ws.send_text("ping")

    hijack = {**ours, "origin": "https://evil.example"}
    with pytest.raises(WebSocketDisconnect) as exc:
        with client.websocket_connect("/ws/global", headers=hijack) as ws:
            ws.receive_text()
    assert exc.value.code == WS_AUTH_CLOSE_CODE
