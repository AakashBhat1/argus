"""Credential extraction, CSRF and WebSocket origin rules."""

from __future__ import annotations

import time

import pytest
from fastapi import FastAPI, Request, WebSocket
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from argus_common.web_auth import (
    ACCESS_COOKIE,
    CSRF_HEADER,
    WS_AUTH_CLOSE_CODE,
    WS_AUTH_SUBPROTOCOL,
    CsrfError,
    cookie_names,
    hold_until_expiry,
    http_credential,
    origin_allowed,
    websocket_credential,
)

NAMES = cookie_names(secure=True)


@pytest.mark.parametrize(
    ("origin", "host", "allowed", "expected"),
    [
        ("https://argus.example.in", "argus.example.in", (), True),
        ("https://argus.example.in", "argus.example.in:443", (), True),
        ("https://argus.example.in:8443", "argus.example.in:8443", (), True),
        # Same host, different port or scheme: a different origin.
        ("https://argus.example.in:8443", "argus.example.in", (), False),
        ("http://argus.example.in", "argus.example.in:443", (), False),
        ("https://evil.example", "argus.example.in", (), False),
        ("https://ARGUS.example.in", "argus.example.in", (), True),
        ("http://localhost:3001", "backend:8000", ("http://localhost:3001",), True),
        ("http://localhost:3002", "backend:8000", ("http://localhost:3001",), False),
        ("null", "argus.example.in", (), False),
        ("", "argus.example.in", (), False),
        ("https://argus.example.in/path", "argus.example.in", (), False),
        ("javascript:alert(1)", "argus.example.in", (), False),
        ("https://argus.example.in:notaport", "argus.example.in", (), False),
    ],
)
def test_origin_allowed(origin, host, allowed, expected):
    assert origin_allowed(origin, host, allowed) is expected


def _app():
    app = FastAPI()

    @app.api_route("/probe", methods=["GET", "POST", "DELETE"])
    async def probe(request: Request):
        try:
            credential = http_credential(request, NAMES, ["http://localhost:3001"])
        except CsrfError as exc:
            return {"error": str(exc)}
        return {"source": credential.source if credential else None}

    @app.websocket("/ws")
    async def ws(websocket: WebSocket):
        credential = websocket_credential(websocket, NAMES, [])
        if credential is None:
            await websocket.close(code=WS_AUTH_CLOSE_CODE)
            return
        await websocket.accept(subprotocol=credential.subprotocol)
        await websocket.send_json({"source": credential.source, "subprotocol": credential.subprotocol})
        await websocket.close()

    @app.websocket("/expiring")
    async def expiring(websocket: WebSocket):
        await websocket.accept()
        await hold_until_expiry(websocket, time.time() + 0.3)

    return app


@pytest.fixture
def client():
    return TestClient(_app(), base_url="https://argus.example.in")


def _cookie(token="jwt"):
    return {"cookie": f"{ACCESS_COOKIE}={token}"}


def test_bearer_header_wins_and_needs_no_csrf_header(client):
    headers = {"authorization": "Bearer abc", **_cookie(), "origin": "https://evil.example"}
    assert client.post("/probe", headers=headers).json() == {"source": "bearer"}


def test_cookie_reads_need_no_csrf_header(client):
    assert client.get("/probe", headers=_cookie()).json() == {"source": "cookie"}


def test_cookie_writes_need_the_csrf_header(client):
    assert client.post("/probe", headers=_cookie()).json() == {"error": "missing CSRF header"}
    assert client.delete("/probe", headers={**_cookie(), CSRF_HEADER: "yes"}).json() == {
        "error": "missing CSRF header"
    }


def test_cookie_writes_from_a_foreign_origin_are_refused(client):
    headers = {**_cookie(), CSRF_HEADER: "1", "origin": "https://evil.example"}
    assert client.post("/probe", headers=headers).json() == {"error": "origin not allowed"}


def test_cookie_writes_from_our_origin_or_allowlist_pass(client):
    for origin in ("https://argus.example.in", "http://localhost:3001"):
        headers = {**_cookie(), CSRF_HEADER: "1", "origin": origin}
        assert client.post("/probe", headers=headers).json() == {"source": "cookie"}
    # Custom header present, no Origin: same-origin from an older browser.
    assert client.post("/probe", headers={**_cookie(), CSRF_HEADER: "1"}).json() == {"source": "cookie"}


def test_no_credentials(client):
    assert client.get("/probe").json() == {"source": None}
    assert client.get("/probe", headers={"authorization": "Basic x"}).json() == {"source": None}


def test_websocket_cookie_from_our_origin(client):
    # (The test client's WebSocket handshake always says Host: testserver.)
    headers = {**_cookie(), "origin": "https://argus.example.in", "host": "argus.example.in"}
    with client.websocket_connect("/ws", headers=headers) as ws:
        assert ws.receive_json() == {"source": "cookie", "subprotocol": None}


def test_websocket_subprotocol_token_is_echoed(client):
    with client.websocket_connect("/ws", subprotocols=[WS_AUTH_SUBPROTOCOL, "jwt"]) as ws:
        assert ws.receive_json() == {"source": "subprotocol", "subprotocol": WS_AUTH_SUBPROTOCOL}


def test_websocket_from_a_foreign_origin_is_refused(client):
    # Cross-site WebSocket hijacking: the browser attaches the cookie anyway.
    with pytest.raises(WebSocketDisconnect) as exc:
        with client.websocket_connect("/ws", headers={**_cookie(), "origin": "https://evil.example"}) as ws:
            ws.receive_json()
    assert exc.value.code == WS_AUTH_CLOSE_CODE
    with pytest.raises(WebSocketDisconnect):
        with client.websocket_connect(
            "/ws", subprotocols=[WS_AUTH_SUBPROTOCOL, "jwt"], headers={"origin": "https://evil.example"}
        ) as ws:
            ws.receive_json()


def test_socket_closes_when_the_token_expires(client):
    with client.websocket_connect("/expiring") as ws:
        with pytest.raises(WebSocketDisconnect) as exc:
            ws.receive_text()
    assert exc.value.code == WS_AUTH_CLOSE_CODE
