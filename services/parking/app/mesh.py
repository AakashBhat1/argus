"""Parking's place in the service mesh.

Receiver-side policy: which scopes each peer may present on parking's
internal API. A peer cannot widen this by minting tokens with more scopes.
"""

from app.config import get_settings
from argus_common.mesh import ServiceMesh

PEER_SCOPES = {
    # Surveillance pushes arming changes to parking.
    "surveillance": {"events:publish"},
}

# Scopes parking requests when calling each peer.
OUTBOUND_SCOPES = {
    "surveillance": ["events:publish", "jwks:read"],
}

mesh = ServiceMesh(get_settings, PEER_SCOPES, debug_provider=lambda: get_settings().DEBUG)
