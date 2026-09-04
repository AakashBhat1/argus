"""smart-parking-system integration and E2E tests.

Validates:
1. Authentication (401 without token) and role authorization (403 for non-admins on mutations).
2. Tenant isolation (Tenant A cannot view, query, or mutate Tenant B's slots/plates/activities).
3. E2E Happy Path (Ingress -> Space Assignment -> Occupancy updates -> Admin Checkout -> Release stats).
"""

from __future__ import annotations

import uuid
from datetime import timedelta

import pytest
import pytest_asyncio
import numpy as np
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from app.models import Camera, User, UserRole, ParkingSpace, DetectedPlate, ParkingActivityLog
from app.services.auth import create_access_token
from app.services import parking_service
from app.services.parking_seeder import seed_parking_spaces_for_tenant
from app.utils import utc_now


def _token_for(user: User) -> str:
    return create_access_token(
        data={
            "sub": user.username,
            "role": user.role,
            "tenant_id": user.tenant_id,
        },
        expires_delta=timedelta(minutes=30),
    )


@pytest_asyncio.fixture(autouse=True)
async def clean_parking_tables(db_session: AsyncSession):
    from sqlalchemy import delete
    from app.models import VehicleProfile, ParkingSpace, DetectedPlate, ParkingSession, ParkingActivityLog
    await db_session.execute(delete(ParkingActivityLog))
    await db_session.execute(delete(ParkingSession))
    await db_session.execute(delete(DetectedPlate))
    await db_session.execute(delete(ParkingSpace))
    await db_session.execute(delete(VehicleProfile))
    await db_session.commit()


@pytest_asyncio.fixture()
async def tenant2_admin(db_session: AsyncSession) -> User:
    user = User(
        id=str(uuid.uuid4()),
        username=f"t2admin_{uuid.uuid4().hex[:6]}",
        hashed_password="somehashpwd",
        role=UserRole.ADMIN.value,
        tenant_id="tenant-2",
        is_active=True,
    )
    db_session.add(user)
    await db_session.flush()
    return user


class TestParkingIntegration:

    @pytest.mark.asyncio
    async def test_auth_enforcement(self, app_with_db, anon_client, operator_user):
        """Verify that anonymous requests fail with 401, and operator fails with 403 on mutations."""
        # 1. Anonymous fails
        response = await anon_client.get("/api/v1/parking/spaces")
        assert response.status_code == 401

        response = await anon_client.get("/api/v1/parking/stats")
        assert response.status_code == 401

        response = await anon_client.get("/api/v1/parking/activity")
        assert response.status_code == 401

        response = await anon_client.post("/api/v1/parking/spaces/G-01/release")
        assert response.status_code == 401

        # 2. Operator can read but NOT mutate
        op_token = _token_for(operator_user)
        async with AsyncClient(
            transport=ASGITransport(app=app_with_db),
            base_url="http://test",
            headers={"Authorization": f"Bearer {op_token}"},
        ) as client:
            # Read stats -> ok
            r_stats = await client.get("/api/v1/parking/stats")
            assert r_stats.status_code == 200

            # Mutate space -> 403 Forbidden
            r_release = await client.post("/api/v1/parking/spaces/G-01/release")
            assert r_release.status_code == 403

            # Command chat -> 403 Forbidden
            r_chat_cmd = await client.post(
                "/api/v1/parking/chat",
                json={"message": "release space G-01", "command": True}
            )
            assert r_chat_cmd.status_code == 403

            r_chat_cmd2 = await client.post(
                "/api/v1/parking/chat/command",
                json={"message": "release space G-01"}
            )
            assert r_chat_cmd2.status_code == 403

    @pytest.mark.asyncio
    async def test_tenant_isolation(
        self,
        app_with_db,
        db_session,
        operator_user,  # tenant-1
        tenant2_user,   # tenant-2
        tenant2_admin,  # tenant-2 admin
    ):
        """Verify cross-tenant read/write isolation."""
        t1_token = _token_for(operator_user)
        t2_token = _token_for(tenant2_user)
        t2_admin_token = _token_for(tenant2_admin)

        # Seed spaces for both tenants
        async with AsyncClient(
            transport=ASGITransport(app=app_with_db),
            base_url="http://test",
        ) as client:
            # Seed tenant 1
            client.headers = {"Authorization": f"Bearer {t1_token}"}
            res = await client.get("/api/v1/parking/spaces")
            assert res.status_code == 200
            t1_spaces = res.json()
            assert len(t1_spaces) == 24
            assert all(s["tenant_id"] == "tenant-1" for s in t1_spaces)

            # Seed tenant 2
            client.headers = {"Authorization": f"Bearer {t2_token}"}
            res = await client.get("/api/v1/parking/spaces")
            assert res.status_code == 200
            t2_spaces = res.json()
            assert len(t2_spaces) == 24
            assert all(s["tenant_id"] == "tenant-2" for s in t2_spaces)

        # Retrieve a specific space ID from tenant-1
        t1_space_id = t1_spaces[0]["space_id"]  # e.g., "G-01"

        # Simulating a vehicle parked in tenant-1's space
        # Using db_session directly to simulate a tenant-1 active session
        async with db_session.begin_nested():
            # Ingest tenant-1 plate
            det_t1 = await parking_service.record_detected_plate(
                db_session, "tenant-1", "MH12AB1234", confidence=0.95
            )
            # Assign tenant-1 space
            await parking_service.assign_space(db_session, "tenant-1", "MH12AB1234")

            # Ingest tenant-2 plate
            det_t2 = await parking_service.record_detected_plate(
                db_session, "tenant-2", "KA03XY9999", confidence=0.98
            )
            # Assign tenant-2 space
            await parking_service.assign_space(db_session, "tenant-2", "KA03XY9999")

            await db_session.commit()

        # 1. Tenant-2 user cannot see Tenant-1's plates
        async with AsyncClient(
            transport=ASGITransport(app=app_with_db),
            base_url="http://test",
            headers={"Authorization": f"Bearer {t2_token}"},
        ) as client:
            res = await client.get("/api/v1/parking/plates")
            assert res.status_code == 200
            t2_plates = res.json()
            assert all(p["tenant_id"] == "tenant-2" for p in t2_plates)
            assert not any(p["plate_text"] == "MH12AB1234" for p in t2_plates)

        # 2. Tenant-2 admin cannot release Tenant-1's space
        async with AsyncClient(
            transport=ASGITransport(app=app_with_db),
            base_url="http://test",
            headers={"Authorization": f"Bearer {t2_admin_token}"},
        ) as client:
            # Try to release tenant-1 space "G-01" using tenant-2 admin token
            res = await client.post(f"/api/v1/parking/spaces/{t1_space_id}/release")
            # Should be 404 since tenant-2 database lookup is scoped to tenant-2
            assert res.status_code == 404

    @pytest.mark.asyncio
    async def test_e2e_parking_happy_path(
        self,
        app_with_db,
        db_session,
        admin_user,  # tenant-1 admin
    ):
        """End-to-end flow: Seed -> Plate Ingress & Assignment -> Read State -> Admin Release."""
        admin_token = _token_for(admin_user)

        async with AsyncClient(
            transport=ASGITransport(app=app_with_db),
            base_url="http://test",
            headers={"Authorization": f"Bearer {admin_token}"},
        ) as client:
            # 1. Check & seed spaces
            r_spaces = await client.get("/api/v1/parking/spaces")
            assert r_spaces.status_code == 200
            spaces = r_spaces.json()
            assert len(spaces) == 24
            assert all(not s["is_occupied"] for s in spaces)

            # 2. Ingest vehicle plate at entry gate
            # Simulate pipeline processing gate_entry camera
            plate_text = "DL1CA9999"
            async with db_session.begin_nested():
                await parking_service.record_detected_plate(
                    db_session, "tenant-1", plate_text, confidence=0.99, camera_id="cam-entry"
                )
                assign_res = await parking_service.assign_space(db_session, "tenant-1", plate_text)
                assert assign_res is not None
                assigned_space_id = assign_res["space_id"]
                await db_session.commit()

            # 3. Read space state via API
            r_spaces_after = await client.get("/api/v1/parking/spaces")
            assert r_spaces_after.status_code == 200
            spaces_after = r_spaces_after.json()

            # Find the assigned space
            matched = [s for s in spaces_after if s["space_id"] == assigned_space_id][0]
            assert matched["is_occupied"] is True
            assert matched["plate_text"] == plate_text

            # Read stats
            r_stats = await client.get("/api/v1/parking/stats")
            assert r_stats.status_code == 200
            stats = r_stats.json()
            assert stats["occupied"] == 1
            assert stats["free"] == 23

            # 4. Release space via admin checkout POST router
            r_release = await client.post(f"/api/v1/parking/spaces/{assigned_space_id}/release")
            assert r_release.status_code == 200
            release_data = r_release.json()
            assert release_data["space_id"] == assigned_space_id
            assert release_data["plate_text"] == plate_text
            assert "duration_minutes" in release_data
            assert "amount_paid" in release_data

            # 5. Verify space is vacant again
            r_spaces_final = await client.get("/api/v1/parking/spaces")
            spaces_final = r_spaces_final.json()
            matched_final = [s for s in spaces_final if s["space_id"] == assigned_space_id][0]
            assert matched_final["is_occupied"] is False
            assert matched_final["plate_text"] is None

            # Verify audit log
            r_act = await client.get("/api/v1/parking/activity")
            assert r_act.status_code == 200
            activities = r_act.json()
            # Most recent activity should be the exit
            assert len(activities) >= 2
            exit_log = [a for a in activities if a["event_type"] == "space_released"][0]
            assert exit_log["space_id"] == assigned_space_id
            assert exit_log["plate_text"] == plate_text
            assert exit_log["actor_user_id"] == admin_user.id

    @pytest.mark.asyncio
    async def test_slot_mapper_auth_tenant_isolation_and_preview(
        self,
        app_with_db,
        db_session,
        admin_user,
        operator_user,
        tenant2_user,
        tenant2_admin,
        monkeypatch,
    ):
        camera_t1 = Camera(
            id=f'parking-t1-{uuid.uuid4().hex}',
            name='Tenant 1 lot',
            location='Lot A',
            stream_url='video://fixture.mp4',
            tenant_id='tenant-1',
            role='parking',
        )
        camera_t2 = Camera(
            id=f'parking-t2-{uuid.uuid4().hex}',
            name='Tenant 2 lot',
            location='Lot B',
            stream_url='video://fixture.mp4',
            tenant_id='tenant-2',
            role='parking',
        )
        db_session.add_all([camera_t1, camera_t2])
        await db_session.commit()

        class FakeCapture:
            def isOpened(self):
                return True

            def read(self):
                frame = np.zeros((128, 64, 3), dtype=np.uint8)
                frame[::8, :] = 255
                frame[:, ::8] = 255
                return True, frame

            def release(self):
                return None

        monkeypatch.setattr(
            'app.routers.parking._open_capture',
            lambda _url: FakeCapture(),
        )
        slot_payload = {
            'slots': [
                {
                    'space_id': 'P-01',
                    'display_order': 0,
                    'polygon': [[0, 0], [0.5, 0], [0.5, 1], [0, 1]],
                },
                {
                    'space_id': 'P-02',
                    'display_order': 1,
                    'polygon': [[0.5, 0], [1, 0], [1, 1], [0.5, 1]],
                },
            ]
        }
        async with AsyncClient(
            transport=ASGITransport(app=app_with_db),
            base_url='http://test',
        ) as client:
            assert (
                await client.get(
                    f'/api/v1/parking/cameras/{camera_t1.id}/slots'
                )
            ).status_code == 401

            client.headers = {
                'Authorization': f'Bearer {_token_for(operator_user)}'
            }
            assert (
                await client.put(
                    f'/api/v1/parking/cameras/{camera_t1.id}/slots',
                    json=slot_payload,
                )
            ).status_code == 403

            client.headers = {'Authorization': f'Bearer {_token_for(admin_user)}'}
            saved = await client.put(
                f'/api/v1/parking/cameras/{camera_t1.id}/slots',
                json=slot_payload,
            )
            assert saved.status_code == 200
            assert [slot['space_id'] for slot in saved.json()] == ['P-01', 'P-02']
            assert all(slot['tenant_id'] == 'tenant-1' for slot in saved.json())
            all_spaces = await client.get('/api/v1/parking/spaces')
            assert all_spaces.status_code == 200
            assert [slot['space_id'] for slot in all_spaces.json()] == ['P-01', 'P-02']

            preview = await client.post(
                '/api/v1/parking/slots/preview',
                json={'camera_id': camera_t1.id, **slot_payload},
            )
            assert preview.status_code == 200
            assert preview.json()['camera_id'] == camera_t1.id
            assert len(preview.json()['slots']) == 2

            invalid = await client.put(
                f'/api/v1/parking/cameras/{camera_t1.id}/slots',
                json={
                    'slots': [
                        {
                            'space_id': 'bad',
                            'polygon': [[0, 0], [1, 0], [1, 1]],
                        }
                    ]
                },
            )
            assert invalid.status_code == 422

            client.headers = {
                'Authorization': f'Bearer {_token_for(tenant2_user)}'
            }
            cross_read = await client.get(
                f'/api/v1/parking/cameras/{camera_t1.id}/slots'
            )
            assert cross_read.status_code == 404

            client.headers = {
                'Authorization': f'Bearer {_token_for(tenant2_admin)}'
            }
            cross_write = await client.put(
                f'/api/v1/parking/cameras/{camera_t1.id}/slots',
                json=slot_payload,
            )
            assert cross_write.status_code == 404

    @pytest.mark.asyncio
    async def test_mapper_preserves_occupied_unmapped_session(
        self,
        app_with_db,
        db_session,
        admin_user,
    ):
        suffix = uuid.uuid4().hex
        camera = Camera(
            id=f'preserve-camera-{suffix}',
            name='Mapped lot',
            location='Lot',
            stream_url='0',
            tenant_id='tenant-1',
            role='parking',
        )
        db_session.add(camera)
        await seed_parking_spaces_for_tenant(db_session, 'tenant-1')
        plate_text = f'KA{suffix[:8].upper()}'
        await parking_service.record_detected_plate(
            db_session, 'tenant-1', plate_text
        )
        assigned = await parking_service.assign_space(
            db_session, 'tenant-1', plate_text
        )
        assert assigned is not None
        seeded = await db_session.get(ParkingSpace, assigned['space_pk'])
        original_entry = utc_now() - timedelta(minutes=42)
        seeded.entry_time = original_entry
        seeded_id = seeded.id
        original_vehicle_id = seeded.vehicle_id
        await db_session.commit()

        payload = {
            'slots': [
                {
                    'space_id': 'P-01',
                    'display_order': 0,
                    'polygon': [[0, 0], [1, 0], [1, 1], [0, 1]],
                }
            ]
        }
        async with AsyncClient(
            transport=ASGITransport(app=app_with_db),
            base_url='http://test',
            headers={
                'Authorization': f'Bearer {_token_for(admin_user)}'
            },
        ) as client:
            response = await client.put(
                f'/api/v1/parking/cameras/{camera.id}/slots',
                json=payload,
            )
        assert response.status_code == 200

        db_session.expire_all()
        preserved = await db_session.get(ParkingSpace, seeded_id)
        assert preserved is not None
        assert preserved.camera_id is None
        assert preserved.is_occupied is True
        assert preserved.vehicle_id == original_vehicle_id
        assert preserved.entry_time == original_entry

    @pytest.mark.asyncio
    async def test_mapper_rejects_replacing_occupied_mapped_bay(
        self,
        app_with_db,
        db_session,
        admin_user,
    ):
        suffix = uuid.uuid4().hex
        camera = Camera(
            id=f'conflict-camera-{suffix}',
            name='Occupied mapped lot',
            location='Lot',
            stream_url='0',
            tenant_id='tenant-1',
            role='parking',
        )
        profile = await parking_service.get_or_create_profile(
            db_session, 'tenant-1', f'TN{suffix[:8].upper()}'
        )
        original_polygon = [[0, 0], [0.5, 0], [0.5, 1], [0, 1]]
        space = ParkingSpace(
            id=f'occupied-space-{suffix}',
            tenant_id='tenant-1',
            camera_id=camera.id,
            space_id='P-01',
            polygon=original_polygon,
            is_occupied=True,
            vehicle_id=profile.id,
            entry_time=utc_now() - timedelta(minutes=15),
        )
        db_session.add_all([camera, space])
        await db_session.commit()
        space_pk = space.id
        profile_id = profile.id
        original_entry = space.entry_time

        payload = {
            'slots': [
                {
                    'space_id': 'P-01',
                    'display_order': 0,
                    'polygon': [[0.5, 0], [1, 0], [1, 1], [0.5, 1]],
                }
            ]
        }
        async with AsyncClient(
            transport=ASGITransport(app=app_with_db),
            base_url='http://test',
            headers={
                'Authorization': f'Bearer {_token_for(admin_user)}'
            },
        ) as client:
            response = await client.put(
                f'/api/v1/parking/cameras/{camera.id}/slots',
                json=payload,
            )
        assert response.status_code == 409
        assert 'P-01' in response.json()['detail']

        db_session.expire_all()
        preserved = await db_session.get(ParkingSpace, space_pk)
        assert preserved is not None
        assert preserved.is_occupied is True
        assert preserved.vehicle_id == profile_id
        assert preserved.entry_time == original_entry
        assert preserved.polygon == original_polygon


@pytest.mark.asyncio
async def test_assign_space_is_idempotent_for_a_parked_vehicle(app_with_db, db_session):
    '''A car re-tracked at the gate must not consume a second space.'''
    await seed_parking_spaces_for_tenant(db_session, 'tenant-1')
    plate = f'KA{uuid.uuid4().hex[:8].upper()}'
    await parking_service.record_detected_plate(db_session, 'tenant-1', plate)
    first = await parking_service.assign_space(db_session, 'tenant-1', plate)
    assert first is not None and not first.get('already_parked')

    await parking_service.record_detected_plate(db_session, 'tenant-1', plate)
    second = await parking_service.assign_space(db_session, 'tenant-1', plate)
    assert second is not None
    assert second['space_id'] == first['space_id']
    assert second['already_parked'] is True

    occupied = (
        await db_session.execute(
            select(ParkingSpace).where(
                ParkingSpace.tenant_id == 'tenant-1', ParkingSpace.is_occupied.is_(True)
            )
        )
    ).scalars().all()
    assert len(occupied) == 1
