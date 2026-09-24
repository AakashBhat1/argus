"""Service identity and peer wiring shared by every Argus service.

Each service has:

* an Ed25519 key that signs its outbound service tokens
  (``SERVICE_SIGNING_KEY_FILE``);
* a directory of peer public keys named ``<peer>.pub.pem``
  (``SERVICE_PEER_KEYS_DIR``), produced by ``deploy/pki.sh``;
* mTLS material for calling peers' internal listeners.

Which scopes a peer may hold is a code-level policy passed by the receiving
service (see each service's ``app/mesh.py``), not configuration a peer can
influence.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Iterable, Mapping, Optional

import httpx
from pydantic_settings import BaseSettings, SettingsConfigDict

from argus_common.keys import KeyConfigError, SigningKey, key_id_for, load_public_pem, load_signing_key
from argus_common.service_http import MtlsConfig, ServiceAuth, internal_client
from argus_common.tokens import PeerPolicy, ServiceTokenSigner, ServiceTokenVerifier

logger = logging.getLogger(__name__)


class ServiceMeshSettings(BaseSettings):
    model_config = SettingsConfigDict(extra="ignore")

    SERVICE_NAME: str = "argus"
    # Ed25519 PEM (chmod 600) signing this service's outbound service tokens.
    SERVICE_SIGNING_KEY_FILE: Optional[str] = None
    # Directory with <peer>.pub.pem public keys of trusted peer services.
    SERVICE_PEER_KEYS_DIR: Optional[str] = None
    # mTLS client material for calls to peers' internal listeners.
    INTERNAL_TLS_CA_FILE: Optional[str] = None
    INTERNAL_TLS_CERT_FILE: Optional[str] = None
    INTERNAL_TLS_KEY_FILE: Optional[str] = None
    # Internal routes require the TLS proxy's "client certificate verified"
    # header. Only single-host development turns this off.
    INTERNAL_REQUIRE_MTLS_HEADER: bool = True
    # Permit http:// peer URLs (single-host dev on a private Docker network).
    INTERNAL_ALLOW_PLAINTEXT: bool = False
    INTERNAL_REQUEST_TIMEOUT_SECONDS: float = 5.0


class ServiceMesh:
    """Lazily builds the signer, verifier and peer clients for a service."""

    def __init__(
        self,
        settings_provider,
        peer_scopes: Mapping[str, Iterable[str]],
        *,
        debug_provider=lambda: False,
    ) -> None:
        self._settings_provider = settings_provider
        self._peer_scopes = {peer: frozenset(scopes) for peer, scopes in peer_scopes.items()}
        self._debug_provider = debug_provider
        self._signer: Optional[ServiceTokenSigner] = None
        self._verifier: Optional[ServiceTokenVerifier] = None
        self.auth = ServiceAuth(self.verifier, self._require_mtls)

    @property
    def settings(self) -> ServiceMeshSettings:
        return self._settings_provider()

    def _require_mtls(self) -> bool:
        return bool(self.settings.INTERNAL_REQUIRE_MTLS_HEADER)

    def signer(self) -> ServiceTokenSigner:
        if self._signer is None:
            settings = self.settings
            try:
                key = load_signing_key(
                    path=settings.SERVICE_SIGNING_KEY_FILE,
                    allow_ephemeral=bool(self._debug_provider()),
                )
            except KeyConfigError as exc:
                raise RuntimeError(
                    f"{settings.SERVICE_NAME}: service signing key unavailable ({exc}); "
                    "set SERVICE_SIGNING_KEY_FILE (see deploy/pki.sh)"
                ) from exc
            self._signer = ServiceTokenSigner(settings.SERVICE_NAME, key)
        return self._signer

    def peer_keys(self, peer: str) -> dict:
        directory = self.settings.SERVICE_PEER_KEYS_DIR
        if not directory:
            return {}
        pem_file = Path(directory) / f"{peer}.pub.pem"
        if not pem_file.is_file():
            return {}
        public = load_public_pem(pem_file.read_bytes())
        return {key_id_for(public): public}

    def verifier(self) -> ServiceTokenVerifier:
        if self._verifier is None:
            peers = {
                peer: PeerPolicy(self.peer_keys(peer), scopes)
                for peer, scopes in self._peer_scopes.items()
            }
            missing = [peer for peer, policy in peers.items() if not policy.keys]
            if missing:
                logger.warning(
                    "%s: no public key for peer(s) %s; their internal calls will be refused",
                    self.settings.SERVICE_NAME,
                    ", ".join(sorted(missing)),
                )
            self._verifier = ServiceTokenVerifier(self.settings.SERVICE_NAME, peers)
        return self._verifier

    def configure_for_tests(
        self, signer: ServiceTokenSigner, verifier: ServiceTokenVerifier
    ) -> None:
        self._signer = signer
        self._verifier = verifier

    def reset(self) -> None:
        self._signer = None
        self._verifier = None

    def client(
        self,
        *,
        peer: str,
        base_url: str,
        scopes: Iterable[str],
        transport: Optional[httpx.AsyncBaseTransport] = None,
    ) -> httpx.AsyncClient:
        settings = self.settings
        return internal_client(
            base_url=base_url,
            signer=self.signer(),
            audience=peer,
            scopes=scopes,
            mtls=MtlsConfig(
                settings.INTERNAL_TLS_CA_FILE,
                settings.INTERNAL_TLS_CERT_FILE,
                settings.INTERNAL_TLS_KEY_FILE,
            ),
            timeout=settings.INTERNAL_REQUEST_TIMEOUT_SECONDS,
            allow_plaintext=bool(settings.INTERNAL_ALLOW_PLAINTEXT) or transport is not None,
            transport=transport,
        )


def generate_test_mesh_keys(*services: str) -> dict[str, SigningKey]:
    """Fresh keys for wiring several services together in tests."""
    return {name: SigningKey.generate() for name in services}
