"""Surveillance's place in the service mesh.

Receiver-side policy: the scopes each peer may present on surveillance's
internal API, and which event types each peer may publish. Peers cannot
widen either by what they put in their tokens or envelopes.
"""

from app.config import get_settings
from argus_common.mesh import ServiceMesh

PEER_SCOPES = {
    "parking": {"events:publish", "jwks:read"},
    "face": {"jwks:read"},
}

# Event types accepted from each peer on POST /internal/v1/events.
ACCEPTED_EVENTS = {
    "parking": {"vehicle.gate_passed", "parking.alert", "vehicle.theft_suspected"},
}

OUTBOUND_SCOPES = {
    "parking": ["events:publish"],
}

mesh = ServiceMesh(get_settings, PEER_SCOPES, debug_provider=lambda: get_settings().DEBUG)
