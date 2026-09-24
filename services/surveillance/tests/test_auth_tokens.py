"""Access tokens are EdDSA, published via JWKS, and bound to the user's tenant."""

from __future__ import annotations

from datetime import timedelta

import jwt
import pytest

from argus_common.keys import SigningKey, public_keys_from_jwks
from argus_common.tokens import StaticKeys, UserTokenIssuer, UserTokenVerifier
from app.services import auth


def _token(user, **overrides):
    data = {"sub": user.username, "role": user.role, "tenant_id": user.tenant_id}
    data.update(overrides)
    return auth.create_access_token(data=data, expires_delta=timedelta(minutes=5))


async def test_jwks_endpoint_publishes_the_signing_key(anon_client, admin_user):
    response = await anon_client.get("/.well-known/jwks.json")
    assert response.status_code == 200
    keys = public_keys_from_jwks(response.json())
    token = _token(admin_user)
    assert jwt.get_unverified_header(token)["alg"] == "EdDSA"
    assert jwt.get_unverified_header(token)["kid"] in keys
    # A peer service can verify the token using only the published JWKS.
    verifier = UserTokenVerifier(StaticKeys(keys), "argus-surveillance", "argus")
    claims = verifier.verify_sync(token)
    assert claims.tenant_id == admin_user.tenant_id


async def test_token_signed_by_another_key_is_rejected(app_with_db, anon_client, admin_user):
    forged = UserTokenIssuer(SigningKey.generate(), "argus-surveillance", "argus").issue(
        subject=admin_user.username, tenant_id=admin_user.tenant_id, role="admin", ttl_seconds=300
    )
    response = await anon_client.get("/api/v1/cameras/", headers={"Authorization": f"Bearer {forged}"})
    assert response.status_code == 401


async def test_token_for_a_different_tenant_is_rejected(anon_client, admin_user):
    token = _token(admin_user, tenant_id="some-other-tenant")
    response = await anon_client.get("/api/v1/cameras/", headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 401


async def test_token_for_deactivated_user_is_rejected(anon_client, admin_user, db_session):
    token = _token(admin_user)
    admin_user.is_active = False
    db_session.add(admin_user)
    await db_session.commit()
    response = await anon_client.get("/api/v1/cameras/", headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 401


def test_tokens_require_a_tenant():
    with pytest.raises(ValueError, match="tenant_id"):
        auth.create_access_token(data={"sub": "x"})


def test_production_refuses_to_start_without_a_signing_key(monkeypatch):
    from app.config import get_settings

    monkeypatch.setenv("DEBUG", "false")
    monkeypatch.delenv("AUTH_SIGNING_KEY_FILE", raising=False)
    get_settings.cache_clear()
    auth.reset_key_cache()
    try:
        with pytest.raises(RuntimeError, match="AUTH_SIGNING_KEY_FILE"):
            auth.signing_key()
    finally:
        get_settings.cache_clear()
        auth.reset_key_cache()


def test_signing_key_file_is_used_and_rotation_keys_verify(tmp_path, monkeypatch):
    from app.config import get_settings

    current, previous = SigningKey.generate(), SigningKey.generate()
    key_file = tmp_path / "auth.key"
    key_file.write_bytes(current.private_pem())
    key_file.chmod(0o600)
    rotation_dir = tmp_path / "previous"
    rotation_dir.mkdir()
    (rotation_dir / "old.pem").write_bytes(previous.public_pem())
    monkeypatch.setenv("AUTH_SIGNING_KEY_FILE", str(key_file))
    monkeypatch.setenv("AUTH_PREVIOUS_PUBLIC_KEYS_DIR", str(rotation_dir))
    get_settings.cache_clear()
    auth.reset_key_cache()
    try:
        assert auth.signing_key().kid == current.kid
        old_token = UserTokenIssuer(previous, "argus-surveillance", "argus").issue(
            subject="u", tenant_id="t", role="operator", ttl_seconds=60
        )
        assert auth.decode_access_token(old_token) is not None
        assert {current.kid, previous.kid} <= set(public_keys_from_jwks(auth.jwks_document()))
    finally:
        get_settings.cache_clear()
        auth.reset_key_cache()
