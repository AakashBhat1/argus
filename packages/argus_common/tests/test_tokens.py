"""User and service token contracts."""

from __future__ import annotations

import time

import pytest

from argus_common.keys import SigningKey, jwks, load_signing_key, public_keys_from_jwks, KeyConfigError
from argus_common.tokens import (
    JwksCache,
    PeerPolicy,
    ReplayCache,
    ScopeError,
    ServiceTokenSigner,
    ServiceTokenVerifier,
    StaticKeys,
    TokenError,
    UserTokenIssuer,
    UserTokenVerifier,
)


def _user_pair(key: SigningKey):
    issuer = UserTokenIssuer(key, issuer="argus-surveillance", audience="argus")
    verifier = UserTokenVerifier(StaticKeys({key.kid: key.public_key}), "argus-surveillance", "argus")
    return issuer, verifier


def test_user_token_round_trip(signing_key):
    issuer, verifier = _user_pair(signing_key)
    token = issuer.issue(subject="alice", tenant_id="t1", role="admin", ttl_seconds=60, user_id="u1")
    claims = verifier.verify_sync(token)
    assert (claims.subject, claims.tenant_id, claims.role, claims.user_id) == ("alice", "t1", "admin", "u1")


def test_user_token_from_other_key_is_rejected(signing_key):
    issuer, _ = _user_pair(SigningKey.generate())
    _, verifier = _user_pair(signing_key)
    token = issuer.issue(subject="alice", tenant_id="t1", role="admin", ttl_seconds=60)
    with pytest.raises(TokenError):
        verifier.verify_sync(token)


def test_expired_user_token_is_rejected(signing_key):
    issuer, verifier = _user_pair(signing_key)
    token = issuer.issue(subject="a", tenant_id="t", role="operator", ttl_seconds=-120)
    with pytest.raises(TokenError, match="expired"):
        verifier.verify_sync(token)


def test_refresh_token_cannot_be_used_as_access(signing_key):
    issuer, verifier = _user_pair(signing_key)
    token = issuer.issue(subject="a", tenant_id="t", role="operator", ttl_seconds=60, token_type="refresh")
    with pytest.raises(TokenError, match="type"):
        verifier.verify_sync(token)


@pytest.mark.parametrize("alg", ["HS256", "none"])
def test_algorithm_confusion_is_rejected(signing_key, alg):
    _, verifier = _user_pair(signing_key)
    payload = {"sub": "a", "tenant_id": "t", "role": "admin", "typ": "access", "iss": "argus-surveillance",
               "aud": "argus", "iat": int(time.time()), "exp": int(time.time()) + 60, "jti": "x"}
    token = _forge(payload, alg, signing_key)
    with pytest.raises(TokenError):
        verifier.verify_sync(token)


def _forge(payload: dict, alg: str, key: SigningKey) -> str:
    """Hand-build the classic confusion attacks PyJWT refuses to create:
    HS256 keyed with the public PEM, and an unsigned "none" token."""
    import base64, hashlib, hmac, json

    def b64(data: bytes) -> str:
        return base64.urlsafe_b64encode(data).rstrip(b"=").decode()

    header = b64(json.dumps({"alg": alg, "typ": "JWT", "kid": key.kid}).encode())
    body = b64(json.dumps(payload).encode())
    signing_input = f"{header}.{body}".encode()
    if alg == "none":
        return f"{header}.{body}."
    sig = hmac.new(key.public_pem(), signing_input, hashlib.sha256).digest()
    return f"{header}.{body}.{b64(sig)}"


def test_wrong_audience_is_rejected(signing_key):
    issuer = UserTokenIssuer(signing_key, issuer="argus-surveillance", audience="other")
    _, verifier = _user_pair(signing_key)
    token = issuer.issue(subject="a", tenant_id="t", role="admin", ttl_seconds=60)
    with pytest.raises(TokenError):
        verifier.verify_sync(token)


async def test_jwks_cache_refreshes_on_unknown_kid(signing_key):
    calls = []

    async def fetch():
        calls.append(1)
        return jwks([(signing_key.public_key, signing_key.kid)])

    cache = JwksCache(fetch, min_refresh_interval=0.0)
    issuer = UserTokenIssuer(signing_key, "iss", "aud")
    verifier = UserTokenVerifier(cache, "iss", "aud", before_verify=cache.ensure)
    token = issuer.issue(subject="a", tenant_id="t", role="operator", ttl_seconds=60)
    assert (await verifier.verify(token)).subject == "a"
    assert (await verifier.verify(token)).subject == "a"
    assert len(calls) == 1


async def test_jwks_refresh_is_rate_limited_against_forged_kids(signing_key):
    calls = []

    async def fetch():
        calls.append(1)
        return jwks([(signing_key.public_key, signing_key.kid)])

    cache = JwksCache(fetch, min_refresh_interval=60.0)
    await cache.ensure()
    for i in range(20):
        await cache.ensure(f"forged-{i}")
    assert len(calls) == 1


def test_jwks_round_trip(signing_key):
    parsed = public_keys_from_jwks(jwks([(signing_key.public_key, signing_key.kid)]))
    assert list(parsed) == [signing_key.kid]


def test_private_key_file_must_not_be_world_readable(tmp_path, signing_key):
    path = tmp_path / "svc.key"
    path.write_bytes(signing_key.private_pem())
    path.chmod(0o644)
    with pytest.raises(KeyConfigError, match="chmod 600"):
        load_signing_key(path=str(path))
    path.chmod(0o600)
    assert load_signing_key(path=str(path)).kid == signing_key.kid


def test_missing_key_requires_explicit_ephemeral_opt_in():
    with pytest.raises(KeyConfigError):
        load_signing_key()
    assert load_signing_key(allow_ephemeral=True).kid


# -- service tokens ------------------------------------------------------------


def _service_pair(allowed=("events:publish",)):
    parking_key = SigningKey.generate()
    signer = ServiceTokenSigner("parking", parking_key)
    verifier = ServiceTokenVerifier(
        "surveillance",
        {"parking": PeerPolicy({parking_key.kid: parking_key.public_key}, frozenset(allowed))},
    )
    return signer, verifier


def test_service_token_round_trip():
    signer, verifier = _service_pair()
    principal = verifier.verify(signer.token_for("surveillance", ["events:publish"]), ["events:publish"])
    assert principal.service == "parking"


def test_service_token_replay_is_rejected():
    signer, verifier = _service_pair()
    token = signer.token_for("surveillance", ["events:publish"])
    verifier.verify(token)
    with pytest.raises(TokenError, match="replayed"):
        verifier.verify(token)


def test_service_token_for_other_audience_is_rejected():
    signer, verifier = _service_pair()
    with pytest.raises(TokenError):
        verifier.verify(signer.token_for("face", ["events:publish"]))


def test_peer_cannot_grant_itself_extra_scopes():
    signer, verifier = _service_pair(allowed=("events:publish",))
    with pytest.raises(ScopeError, match="not granted"):
        verifier.verify(signer.token_for("surveillance", ["events:publish", "face:admin"]))


def test_missing_required_scope_is_rejected():
    signer, verifier = _service_pair(allowed=("events:publish", "face:identify:gate"))
    with pytest.raises(ScopeError, match="missing"):
        verifier.verify(signer.token_for("surveillance", ["events:publish"]), ["face:identify:gate"])


def test_unknown_peer_and_forged_issuer_are_rejected():
    signer, verifier = _service_pair()
    rogue = ServiceTokenSigner("parking", SigningKey.generate())
    with pytest.raises(TokenError):
        verifier.verify(rogue.token_for("surveillance", ["events:publish"]))
    stranger = ServiceTokenSigner("stranger", SigningKey.generate())
    with pytest.raises(TokenError, match="unknown peer"):
        verifier.verify(stranger.token_for("surveillance", ["events:publish"]))


def test_user_token_is_not_a_service_token(signing_key):
    signer, _ = _service_pair()
    verifier = ServiceTokenVerifier(
        "argus", {"argus-surveillance": PeerPolicy({signing_key.kid: signing_key.public_key}, frozenset())}
    )
    user_token = UserTokenIssuer(signing_key, "argus-surveillance", "argus").issue(
        subject="argus-surveillance", tenant_id="t", role="admin", ttl_seconds=60
    )
    with pytest.raises(TokenError):
        verifier.verify(user_token)


def test_long_lived_service_tokens_are_refused():
    with pytest.raises(ValueError):
        ServiceTokenSigner("parking", SigningKey.generate(), ttl_seconds=3600)


def test_replay_cache_forgets_expired_ids():
    cache = ReplayCache()
    assert cache.check_and_remember("a", time.time() - 3600)
    assert cache.check_and_remember("a", time.time() + 60)
    assert not cache.check_and_remember("a", time.time() + 60)
