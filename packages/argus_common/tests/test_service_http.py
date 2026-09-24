"""Internal-route guard and the outbound internal client."""

from __future__ import annotations

import httpx
import pytest
from fastapi import Depends, FastAPI

from argus_common.keys import SigningKey
from argus_common.service_http import (
    MTLS_CLIENT_SERVICE_HEADER,
    MTLS_VERIFIED_HEADER,
    SERVICE_TOKEN_HEADER,
    MtlsConfig,
    ServiceAuth,
    internal_client,
)
from argus_common.tokens import PeerPolicy, ServiceTokenSigner, ServiceTokenVerifier


def _app(require_mtls: bool = False):
    key = SigningKey.generate()
    signer = ServiceTokenSigner("parking", key)
    verifier = ServiceTokenVerifier(
        "surveillance", {"parking": PeerPolicy({key.kid: key.public_key}, frozenset({"events:publish"}))}
    )
    guard = ServiceAuth(lambda: verifier, lambda: require_mtls)
    app = FastAPI()

    @app.post("/internal/v1/events")
    async def events(principal=Depends(guard.require("events:publish"))):
        return {"from": principal.service}

    return app, signer


async def test_internal_client_attaches_fresh_token_per_request():
    app, signer = _app()
    client = internal_client(
        base_url="http://surveillance",
        signer=signer,
        audience="surveillance",
        scopes=["events:publish"],
        mtls=MtlsConfig(),
        allow_plaintext=True,
        transport=httpx.ASGITransport(app=app),
    )
    async with client:
        for _ in range(3):  # tokens are single-use; each request must mint one
            response = await client.post("/internal/v1/events", json={})
            assert response.status_code == 200, response.text
            assert response.json() == {"from": "parking"}


async def test_missing_or_user_token_is_rejected():
    app, _ = _app()
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://s") as client:
        assert (await client.post("/internal/v1/events")).status_code == 401
        response = await client.post(
            "/internal/v1/events", headers={"Authorization": "Bearer something", SERVICE_TOKEN_HEADER: "junk"}
        )
        assert response.status_code == 401


async def test_mtls_header_required_when_configured():
    app, signer = _app(require_mtls=True)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://s") as client:
        token = signer.token_for("surveillance", ["events:publish"])
        assert (await client.post("/internal/v1/events", headers={SERVICE_TOKEN_HEADER: token})).status_code == 403
        token = signer.token_for("surveillance", ["events:publish"])
        ok = await client.post(
            "/internal/v1/events",
            headers={SERVICE_TOKEN_HEADER: token, MTLS_VERIFIED_HEADER: "SUCCESS", MTLS_CLIENT_SERVICE_HEADER: "parking"},
        )
        assert ok.status_code == 200
        # A parking token presented over the face service's certificate.
        token = signer.token_for("surveillance", ["events:publish"])
        mismatched = await client.post(
            "/internal/v1/events",
            headers={SERVICE_TOKEN_HEADER: token, MTLS_VERIFIED_HEADER: "SUCCESS", MTLS_CLIENT_SERVICE_HEADER: "face"},
        )
        assert mismatched.status_code == 403


def test_plaintext_internal_endpoints_are_refused_by_default():
    signer = ServiceTokenSigner("parking", SigningKey.generate())
    with pytest.raises(ValueError, match="non-TLS"):
        internal_client(base_url="http://surveillance", signer=signer, audience="s", scopes=[], mtls=MtlsConfig())
    with pytest.raises(ValueError, match="mTLS"):
        internal_client(base_url="https://surveillance", signer=signer, audience="s", scopes=[], mtls=MtlsConfig())
