"""Real TLS handshakes for the internal client (no mocks).

A tiny HTTPS server requiring client certificates stands in for a peer's
internal listener. The client must present a certificate from the private CA
and must refuse a server whose certificate chains to a different CA.
"""

from __future__ import annotations

import asyncio
import datetime as dt
import ssl
from pathlib import Path

import httpx
import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID

from argus_common.keys import SigningKey
from argus_common.service_http import MtlsConfig, internal_client
from argus_common.tokens import ServiceTokenSigner


def _name(cn: str) -> x509.Name:
    return x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, cn)])


def _ca(tmp: Path, label: str):
    key = ec.generate_private_key(ec.SECP256R1())
    now = dt.datetime.now(dt.timezone.utc)
    cert = (
        x509.CertificateBuilder()
        .subject_name(_name(f"{label} CA"))
        .issuer_name(_name(f"{label} CA"))
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - dt.timedelta(minutes=1))
        .not_valid_after(now + dt.timedelta(days=1))
        .add_extension(x509.BasicConstraints(ca=True, path_length=0), critical=True)
        .sign(key, hashes.SHA256())
    )
    path = tmp / f"{label}-ca.pem"
    path.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    return key, cert, path


def _leaf(tmp: Path, ca_key, ca_cert, cn: str):
    key = ec.generate_private_key(ec.SECP256R1())
    now = dt.datetime.now(dt.timezone.utc)
    cert = (
        x509.CertificateBuilder()
        .subject_name(_name(cn))
        .issuer_name(ca_cert.subject)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - dt.timedelta(minutes=1))
        .not_valid_after(now + dt.timedelta(days=1))
        .add_extension(x509.SubjectAlternativeName([x509.DNSName("localhost")]), critical=False)
        .add_extension(
            x509.ExtendedKeyUsage([ExtendedKeyUsageOID.SERVER_AUTH, ExtendedKeyUsageOID.CLIENT_AUTH]),
            critical=False,
        )
        .sign(ca_key, hashes.SHA256())
    )
    cert_path, key_path = tmp / f"{cn}.pem", tmp / f"{cn}.key"
    cert_path.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    key_path.write_bytes(
        key.private_bytes(
            serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()
        )
    )
    return cert_path, key_path


async def _serve(server_cert, server_key, trusted_ca):
    context = ssl.create_default_context(ssl.Purpose.CLIENT_AUTH, cafile=str(trusted_ca))
    context.load_cert_chain(str(server_cert), str(server_key))
    context.verify_mode = ssl.CERT_REQUIRED
    seen: list[str] = []

    async def handle(reader, writer):
        try:
            peer = writer.get_extra_info("peercert") or {}
            subject = dict(item[0] for item in peer.get("subject", ()))
            seen.append(subject.get("commonName", ""))
            await reader.readuntil(b"\r\n\r\n")
            writer.write(b"HTTP/1.1 200 OK\r\nContent-Length: 2\r\nConnection: close\r\n\r\nok")
            await writer.drain()
        except (asyncio.IncompleteReadError, ConnectionError, ssl.SSLError):
            pass
        finally:
            writer.close()

    server = await asyncio.start_server(handle, "127.0.0.1", 0, ssl=context)
    port = server.sockets[0].getsockname()[1]
    return server, port, seen


@pytest.fixture
def pki(tmp_path):
    ca_key, ca_cert, ca_path = _ca(tmp_path, "argus")
    rogue_key, rogue_cert, _ = _ca(tmp_path, "rogue")
    return {
        "ca": ca_path,
        "server": _leaf(tmp_path, ca_key, ca_cert, "surveillance"),
        "client": _leaf(tmp_path, ca_key, ca_cert, "parking"),
        "rogue_server": _leaf(tmp_path, rogue_key, rogue_cert, "imposter"),
    }


def _client(port, pki):
    client_cert, client_key = pki["client"]
    return internal_client(
        base_url=f"https://localhost:{port}",
        signer=ServiceTokenSigner("parking", SigningKey.generate()),
        audience="surveillance",
        scopes=["events:publish"],
        mtls=MtlsConfig(str(pki["ca"]), str(client_cert), str(client_key)),
    )


async def test_mutual_tls_handshake_succeeds_with_ca_issued_certs(pki):
    server, port, seen = await _serve(*pki["server"], pki["ca"])
    async with server, _client(port, pki) as client:
        response = await client.get("/internal/v1/health")
    assert response.status_code == 200
    assert seen == ["parking"]


async def test_client_without_certificate_is_refused(pki):
    server, port, _ = await _serve(*pki["server"], pki["ca"])
    context = ssl.create_default_context(cafile=str(pki["ca"]))
    async with server, httpx.AsyncClient(verify=context) as anonymous:
        with pytest.raises((httpx.ConnectError, httpx.ReadError, httpx.RemoteProtocolError)):
            await anonymous.get(f"https://localhost:{port}/internal/v1/health")


async def test_client_refuses_a_server_from_another_ca(pki):
    server, port, _ = await _serve(*pki["rogue_server"], pki["ca"])
    async with server, _client(port, pki) as client:
        with pytest.raises(httpx.ConnectError):
            await client.get("/internal/v1/health")


def test_openssl_generated_ed25519_keys_load(tmp_path):
    """deploy/pki.sh writes PKCS#8 Ed25519 keys via openssl genpkey."""
    import shutil
    import subprocess

    from argus_common.keys import load_signing_key

    if shutil.which("openssl") is None:
        pytest.skip("openssl not available")
    key_path = tmp_path / "service.key"
    subprocess.run(["openssl", "genpkey", "-algorithm", "ed25519", "-out", str(key_path)], check=True)
    key_path.chmod(0o600)
    assert load_signing_key(path=str(key_path)).kid
