"""Outbound network target validation (SSRF defence) for camera sources.

Camera stream URLs are operator input that the server later connects to, so
they are a server-side request forgery vector. The rules:

* Only ``rtsp``/``rtsps`` by default; ``http(s)`` needs ``ALLOW_HTTP_STREAMS``.
* The hostname is resolved and **every** resolved address is checked, so a DNS
  name pointing at an internal address is treated like the literal address.
* Loopback, link-local, unspecified, multicast, broadcast and cloud metadata
  addresses are always rejected unless the exact range is allowlisted (the
  metadata addresses can never be allowlisted).
* Private-LAN addresses (RFC 1918 / ULA / CGNAT) are rejected unless they fall
  inside ``CAMERA_NETWORK_ALLOWLIST``. On-prem deployments list their camera
  VLAN there, e.g. ``["192.168.10.0/24"]``.
* ``STREAM_HOST_ALLOWLIST`` names internal media hosts (``mediamtx:8554``)
  that may resolve to container addresses; the port must match.

``validate_stream_target`` runs when a camera is saved; ``recheck_stream_target``
runs again right before a connection is opened, which closes the window for
DNS rebinding between validation and use.
"""

from __future__ import annotations

import ipaddress
import re
import socket
from dataclasses import dataclass
from typing import Callable, Iterable, Optional, Sequence
from urllib.parse import urlsplit, urlunsplit

IPAddress = ipaddress.IPv4Address | ipaddress.IPv6Address
IPNetwork = ipaddress.IPv4Network | ipaddress.IPv6Network

STREAM_SCHEMES = frozenset({"rtsp", "rtsps"})
HTTP_SCHEMES = frozenset({"http", "https"})
DEFAULT_PORTS = {"rtsp": 554, "rtsps": 322, "http": 80, "https": 443}

# Never reachable, whatever the configuration says.
_METADATA_ADDRESSES: frozenset[IPAddress] = frozenset(
    {
        ipaddress.ip_address("169.254.169.254"),  # AWS / GCP / Azure / OpenStack
        ipaddress.ip_address("169.254.170.2"),  # AWS ECS task metadata
        ipaddress.ip_address("100.100.100.200"),  # Alibaba Cloud
        ipaddress.ip_address("fd00:ec2::254"),  # AWS IMDS over IPv6
    }
)

# Blocked unless the operator allowlists the specific range.
_SPECIAL_NETWORKS: tuple[IPNetwork, ...] = tuple(
    ipaddress.ip_network(cidr)
    for cidr in (
        "0.0.0.0/8",
        "127.0.0.0/8",
        "169.254.0.0/16",
        "224.0.0.0/4",
        "240.0.0.0/4",
        "255.255.255.255/32",
        "::/128",
        "::1/128",
        "fe80::/10",
        "ff00::/8",
    )
)

# Private LANs: allowed only when inside CAMERA_NETWORK_ALLOWLIST.
_PRIVATE_NETWORKS: tuple[IPNetwork, ...] = tuple(
    ipaddress.ip_network(cidr)
    for cidr in (
        "10.0.0.0/8",
        "172.16.0.0/12",
        "192.168.0.0/16",
        "100.64.0.0/10",
        "fc00::/7",
    )
)

RFC1918_NETWORKS: tuple[str, ...] = ("10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16")

Resolver = Callable[[str, int], Sequence[str]]


class StreamTargetError(ValueError):
    """Raised when a stream URL targets a forbidden scheme, host or address."""


@dataclass(frozen=True)
class StreamTargetPolicy:
    allow_http: bool = False
    network_allowlist: tuple[IPNetwork, ...] = ()
    host_allowlist: frozenset[tuple[str, int]] = frozenset()

    @classmethod
    def build(
        cls,
        *,
        allow_http: bool = False,
        network_allowlist: Iterable[str] = (),
        host_allowlist: Iterable[str] = (),
    ) -> "StreamTargetPolicy":
        networks: list[IPNetwork] = []
        for cidr in network_allowlist:
            text = str(cidr).strip()
            if not text:
                continue
            try:
                networks.append(ipaddress.ip_network(text, strict=False))
            except ValueError as exc:
                raise ValueError(f"Invalid CIDR in CAMERA_NETWORK_ALLOWLIST: {text!r}") from exc
        hosts: set[tuple[str, int]] = set()
        for entry in host_allowlist:
            text = str(entry).strip().lower()
            if not text:
                continue
            host, sep, port = text.rpartition(":")
            if not sep or not host or not port.isdigit():
                raise ValueError(
                    f"STREAM_HOST_ALLOWLIST entries must be host:port, got {entry!r}"
                )
            hosts.add((host, int(port)))
        return cls(
            allow_http=bool(allow_http),
            network_allowlist=tuple(networks),
            host_allowlist=frozenset(hosts),
        )


@dataclass(frozen=True)
class StreamTarget:
    scheme: str
    host: str
    port: int
    addresses: tuple[str, ...]
    trusted_host: bool


def default_resolver(host: str, port: int) -> Sequence[str]:
    infos = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    return sorted({str(info[4][0]) for info in infos})


def _normalise(address: IPAddress) -> IPAddress:
    if isinstance(address, ipaddress.IPv6Address) and address.ipv4_mapped is not None:
        return address.ipv4_mapped
    return address


def _in_any(address: IPAddress, networks: Iterable[IPNetwork]) -> bool:
    return any(address.version == net.version and address in net for net in networks)


def check_address(address: IPAddress | str, policy: StreamTargetPolicy) -> None:
    """Raise ``StreamTargetError`` when ``address`` may not be contacted."""
    ip = _normalise(ipaddress.ip_address(address) if isinstance(address, str) else address)
    if ip in _METADATA_ADDRESSES:
        raise StreamTargetError("Stream URLs may not target cloud metadata services.")
    allowlisted = _in_any(ip, policy.network_allowlist)
    if _in_any(ip, _SPECIAL_NETWORKS) and not allowlisted:
        raise StreamTargetError(
            "Stream URLs targeting loopback, link-local, multicast or reserved "
            "addresses are not allowed."
        )
    if _in_any(ip, _PRIVATE_NETWORKS) and not allowlisted:
        raise StreamTargetError(
            "Stream URLs targeting private network addresses are not allowed. "
            "Add the camera network to CAMERA_NETWORK_ALLOWLIST (CIDR) to permit it."
        )


def validate_stream_target(
    url: str,
    policy: StreamTargetPolicy,
    resolver: Optional[Resolver] = None,
) -> StreamTarget:
    """Validate a network stream URL and return its resolved target."""
    value = (url or "").strip()
    try:
        parts = urlsplit(value)
        port = parts.port
    except ValueError as exc:
        raise StreamTargetError("Malformed stream URL.") from exc
    scheme = parts.scheme.lower()
    allowed = STREAM_SCHEMES | HTTP_SCHEMES if policy.allow_http else STREAM_SCHEMES
    if scheme not in allowed:
        raise StreamTargetError(
            f"Unsupported URL scheme '{scheme or '(none)'}'. "
            f"Allowed: {', '.join(sorted(allowed))}, or video://."
        )
    host = (parts.hostname or "").strip().lower().rstrip(".")
    if not host:
        raise StreamTargetError("Stream URL must include a host.")
    if port is None:
        port = DEFAULT_PORTS[scheme]
    if not 0 < port < 65536:
        raise StreamTargetError("Stream URL port is out of range.")

    trusted_host = (host, port) in policy.host_allowlist
    try:
        literal: Optional[IPAddress] = ipaddress.ip_address(host)
    except ValueError:
        literal = None

    if literal is not None:
        addresses = [str(literal)]
    else:
        try:
            addresses = list((resolver or default_resolver)(host, port))
        except (OSError, UnicodeError) as exc:
            raise StreamTargetError(f"Stream host '{host}' could not be resolved.") from exc
        if not addresses:
            raise StreamTargetError(f"Stream host '{host}' could not be resolved.")

    for address in addresses:
        ip = _normalise(ipaddress.ip_address(address))
        if ip in _METADATA_ADDRESSES:
            raise StreamTargetError("Stream URLs may not target cloud metadata services.")
        if not trusted_host:
            check_address(ip, policy)

    return StreamTarget(
        scheme=scheme,
        host=host,
        port=port,
        addresses=tuple(addresses),
        trusted_host=trusted_host,
    )


def recheck_stream_target(
    url: str,
    policy: StreamTargetPolicy,
    resolver: Optional[Resolver] = None,
) -> StreamTarget:
    """Connect-time re-validation (defeats DNS rebinding after save)."""
    return validate_stream_target(url, policy, resolver)


def policy_from_settings(settings) -> StreamTargetPolicy:
    """Build the stream target policy from application settings."""
    networks = list(getattr(settings, "CAMERA_NETWORK_ALLOWLIST", []) or [])
    allow_http = bool(getattr(settings, "ALLOW_HTTP_STREAMS", False))
    if getattr(settings, "ALLOW_PRIVATE_STREAM_URLS", False):
        networks.extend(RFC1918_NETWORKS)
        allow_http = True
    return StreamTargetPolicy.build(
        allow_http=allow_http,
        network_allowlist=networks,
        host_allowlist=getattr(settings, "STREAM_HOST_ALLOWLIST", []) or [],
    )


def is_network_source(value: str) -> bool:
    """True when ``value`` looks like a URL the server would connect to."""
    scheme = (value or "").strip().partition(":")[0].lower()
    return "://" in (value or "") and scheme not in {"video", "file"}


_SECRET_KEYS = ("password", "passwd", "pwd", "pass", "token", "secret", "key")


def redact_url(url: object) -> str:
    """Return ``url`` with credentials removed, safe for logs and API output.

    Strips ``user:pass@`` userinfo and masks ``password=...``-style parameters,
    which some cameras embed in the RTSP path rather than the query string.
    """
    text = str(url or "")
    try:
        parts = urlsplit(text)
    except ValueError:
        return "<unparseable url>"
    if not parts.scheme or not parts.netloc:
        return text
    netloc = parts.netloc.rpartition("@")[2]
    if parts.username is not None:
        netloc = f"***@{netloc}"
    rest = text[len(parts.scheme) + 3 + len(parts.netloc):]
    rest = re.sub(
        r"(?i)((?:%s)=)[^&;/?#]*" % "|".join(_SECRET_KEYS),
        r"\1***",
        rest,
    )
    return f"{parts.scheme}://{netloc}{rest}"


REDACTION_MASK = "***"
_MASKED_PARAM = re.compile(r"(?i)\b(%s)=\*\*\*" % "|".join(_SECRET_KEYS))
_SECRET_PARAM = re.compile(r"(?i)\b(%s)=([^&;/?#]*)" % "|".join(_SECRET_KEYS))


class MaskedCredentialsError(ValueError):
    """A masked URL was sent back for a different address."""


def restore_masked_credentials(submitted: str, current: str) -> str:
    """Undo the API's redaction in a URL a client sent back.

    Clients only ever see ``redact_url`` output. When they send it back
    (editing a camera's name, or its path) the stored credentials must be
    kept rather than replaced by the mask. They are restored only for the
    same scheme, host and port: moving a camera to another address needs the
    credentials typed again, so nobody can have them sent to a server of
    their choosing.
    """
    if REDACTION_MASK not in submitted:
        return submitted
    try:
        new, old = urlsplit(submitted), urlsplit(current)
        same_address = (new.scheme, new.hostname, new.port) == (old.scheme, old.hostname, old.port)
    except ValueError as exc:
        raise MaskedCredentialsError("unparseable URL") from exc
    if not same_address:
        raise MaskedCredentialsError("re-enter the camera credentials when changing its address")

    result = submitted
    userinfo, at, host = new.netloc.rpartition("@")
    if at and userinfo == REDACTION_MASK:
        old_userinfo = old.netloc.rpartition("@")[0]
        netloc = f"{old_userinfo}@{host}" if old_userinfo else host
        result = urlunsplit(new._replace(netloc=netloc))

    old_values = {key.lower(): value for key, value in _SECRET_PARAM.findall(current)}

    def put_back(match: "re.Match[str]") -> str:
        value = old_values.get(match.group(1).lower())
        if value is None:
            raise MaskedCredentialsError(f"no stored value for {match.group(1)}")
        return f"{match.group(1)}={value}"

    result = _MASKED_PARAM.sub(put_back, result)
    if REDACTION_MASK in urlsplit(result).netloc:
        raise MaskedCredentialsError("masked credentials cannot be restored")
    return result
