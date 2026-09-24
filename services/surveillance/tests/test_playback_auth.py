"""Edge auth_request hook for WebRTC playback of surveillance cameras."""

from __future__ import annotations

import uuid

from app.models import Camera
from argus_common.web_auth import STREAM_URI_HEADER

URL = "/api/v1/streams/playback-auth"


def _uri(camera_id: str) -> dict:
    return {STREAM_URI_HEADER: f"/stream/{camera_id}/whep"}


async def test_owner_tenant_may_play(auth_client, sample_camera, db_session):
    await db_session.commit()
    assert (await auth_client.get(URL, headers=_uri(sample_camera.id))).status_code == 204
    # nginx may issue the subrequest with the browser's method.
    assert (await auth_client.post(URL, headers=_uri(sample_camera.id))).status_code == 204


async def test_other_tenants_cameras_are_refused(auth_client, db_session):
    foreign = Camera(id=str(uuid.uuid4()), name="Other", location="X", stream_url="rtsp://203.0.113.9/l", tenant_id="tenant-2")
    db_session.add(foreign)
    await db_session.commit()
    assert (await auth_client.get(URL, headers=_uri(foreign.id))).status_code == 403
    assert (await auth_client.get(URL, headers=_uri(str(uuid.uuid4())))).status_code == 403


async def test_non_whep_paths_are_refused(auth_client, sample_camera, db_session):
    await db_session.commit()
    for uri in (f"/stream/{sample_camera.id}/", f"/stream/{sample_camera.id}/x/whep", ""):
        assert (await auth_client.get(URL, headers={STREAM_URI_HEADER: uri})).status_code == 403


async def test_anonymous_viewers_are_refused(anon_client, sample_camera, db_session):
    await db_session.commit()
    assert (await anon_client.get(URL, headers=_uri(sample_camera.id))).status_code == 401
