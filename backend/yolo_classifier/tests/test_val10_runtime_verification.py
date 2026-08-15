import pytest
import pytest_asyncio
import uuid
from datetime import datetime, timedelta, timezone
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

from app.models import Camera, ParkingSpace, DetectedPlate, VehicleProfile, ParkingActivityLog, User, UserRole
from app.services.auth import create_access_token
from app.services.parking_service import assign_space, release_space
from app.services.parking_occupancy_service import apply_occupancy_tick, SlotTransition


@pytest_asyncio.fixture
async def admin_val10(db_session):
    uname = f"val10_admin_{uuid.uuid4().hex[:6]}"
    user = User(
        id=str(uuid.uuid4()),
        tenant_id=f"tenant_val10_{uuid.uuid4().hex[:4]}",
        username=uname,
        hashed_password="hashed_pwd",
        role=UserRole.ADMIN.value,
        is_active=True,
    )
    db_session.add(user)
    await db_session.commit()
    return user


@pytest_asyncio.fixture
async def user_val10_b(db_session):
    uname = f"val10_user_b_{uuid.uuid4().hex[:6]}"
    user = User(
        id=str(uuid.uuid4()),
        tenant_id=f"tenant_val10_b_{uuid.uuid4().hex[:4]}",
        username=uname,
        hashed_password="hashed_pwd",
        role=UserRole.OPERATOR.value,
        is_active=True,
    )
    db_session.add(user)
    await db_session.commit()
    return user


@pytest_asyncio.fixture
async def client_a(app_with_db, admin_val10):
    token = create_access_token({"sub": admin_val10.username, "tenant_id": admin_val10.tenant_id, "role": admin_val10.role})
    async with AsyncClient(
        transport=ASGITransport(app=app_with_db),
        base_url="http://test",
        headers={"Authorization": f"Bearer {token}"},
    ) as client:
        yield client


@pytest_asyncio.fixture
async def client_b(app_with_db, user_val10_b):
    token = create_access_token({"sub": user_val10_b.username, "tenant_id": user_val10_b.tenant_id, "role": user_val10_b.role})
    async with AsyncClient(
        transport=ASGITransport(app=app_with_db),
        base_url="http://test",
        headers={"Authorization": f"Bearer {token}"},
    ) as client:
        yield client


@pytest.mark.asyncio
async def test_val10_steps_3_4_6_slot_mapping_preview_stats(client_a):
    # Step 3: Create camera with role='parking' and stream_url='video://istockphoto-1370353417-640_adpp_is_slower_8x.mp4'
    cam_data = {
        "name": "Val10 Parking Cam",
        "location": "North Lot",
        "stream_url": "video://istockphoto-1370353417-640_adpp_is_slower_8x.mp4",
        "role": "parking",
        "status": "active",
        "is_active": True,
    }
    res_cam = await client_a.post("/api/v1/cameras/", json=cam_data)
    assert res_cam.status_code in (200, 201), res_cam.text
    cam_id = res_cam.json()["id"]

    # Step 4: Map bays P-01..P-26 (2 rows of 13 quads)
    slots_payload = []
    for i in range(1, 14):
        x1, x2 = (i - 1) * 0.07, i * 0.07
        slots_payload.append({
            "space_id": f"P-{i:02d}",
            "polygon": [[x1, 0.1], [x2, 0.1], [x2, 0.3], [x1, 0.3]]
        })
    for i in range(14, 27):
        x1, x2 = (i - 14) * 0.07, (i - 13) * 0.07
        slots_payload.append({
            "space_id": f"P-{i:02d}",
            "polygon": [[x1, 0.6], [x2, 0.6], [x2, 0.8], [x1, 0.8]]
        })

    # Save slots via PUT /parking/cameras/{id}/slots
    res_save = await client_a.put(f"/api/v1/parking/cameras/{cam_id}/slots", json={"slots": slots_payload})
    assert res_save.status_code == 200, res_save.text
    assert len(res_save.json()) == 26

    # Preview scores via POST /parking/slots/preview
    res_prev = await client_a.post("/api/v1/parking/slots/preview", json={"camera_id": cam_id, "slots": slots_payload})
    assert res_prev.status_code == 200, res_prev.text
    prev_slots = res_prev.json()["slots"]
    assert len(prev_slots) == 26
    scores = [s["score"] for s in prev_slots]
    assert all(0.0 <= s <= 1.0 for s in scores)

    # Step 6: GET /parking/stats free/occupied counts match
    res_stats = await client_a.get("/api/v1/parking/stats")
    assert res_stats.status_code == 200
    stats = res_stats.json()
    assert stats["total"] == 26
    assert stats["free"] + stats["occupied"] == 26


@pytest.mark.asyncio
async def test_val10_step_9_tenant_isolation(client_a, client_b):
    # Create camera on tenant A
    cam_data = {"name": "Tenant A Cam", "location": "Lot A", "stream_url": "rtsp://test", "role": "parking", "is_active": True}
    res_cam = await client_a.post("/api/v1/cameras/", json=cam_data)
    assert res_cam.status_code in (200, 201), res_cam.text
    cam_id = res_cam.json()["id"]

    # Save slots on tenant A
    slots_payload = [{"space_id": "P-01", "polygon": [[0.1, 0.1], [0.2, 0.1], [0.2, 0.2], [0.1, 0.2]]}]
    await client_a.put(f"/api/v1/parking/cameras/{cam_id}/slots", json={"slots": slots_payload})

    # Tenant B tries to GET slots for Tenant A camera -> 404
    res_b_slots = await client_b.get(f"/api/v1/parking/cameras/{cam_id}/slots")
    assert res_b_slots.status_code == 404

    # Tenant B GET /parking/spaces sees NONE of Tenant A's slots
    res_b_spaces = await client_b.get("/api/v1/parking/spaces")
    spaces_b = res_b_spaces.json()
    assert not any(s["space_id"] == "P-01" for s in spaces_b)


@pytest.mark.asyncio
async def test_val10_step_10_vision_checkout(app_with_db, db_session):
    tenant_id = f"tenant_val10_checkout_{uuid.uuid4().hex[:4]}"
    cam_id = f"val10-cam-checkout-{uuid.uuid4().hex[:4]}"

    space = ParkingSpace(
        id=str(uuid.uuid4()),
        tenant_id=tenant_id,
        camera_id=cam_id,
        space_id="P-10",
        polygon=[[0.1, 0.1], [0.2, 0.1], [0.2, 0.2], [0.1, 0.2]],
        is_occupied=False,
    )
    db_session.add(space)

    plate = DetectedPlate(
        id=str(uuid.uuid4()),
        tenant_id=tenant_id,
        plate_text="VAL10OK",
        confidence=0.95,
        timestamp=datetime.now(timezone.utc) - timedelta(minutes=120),
        is_parked=True,
    )
    db_session.add(plate)

    profile = VehicleProfile(
        id=str(uuid.uuid4()),
        tenant_id=tenant_id,
        plate_text="VAL10OK",
    )
    db_session.add(profile)
    await db_session.commit()

    # Assign space to plate (simulating gate OCR entry)
    res_assign = await assign_space(db_session, tenant_id, "VAL10OK")
    assert res_assign is not None

    # Backdate entry_time by 120 minutes
    await db_session.refresh(space)
    space.entry_time = datetime.now(timezone.utc) - timedelta(minutes=120)
    await db_session.commit()

    # Vision reports vacate transition
    transitions = [
        SlotTransition(
            space_id="P-10",
            db_id=space.id,
            occupied=False,
            score=0.02,
            source="vision",
        )
    ]
    await apply_occupancy_tick(cam_id, tenant_id, transitions)

    # Assert checkout occurred
    await db_session.refresh(space)
    assert space.is_occupied is False
    assert space.entry_time is None
    assert space.vehicle_id is None

    await db_session.refresh(plate)
    assert plate.is_parked is False
    assert plate.exit_time is not None
    assert plate.duration_minutes is not None and plate.duration_minutes >= 119
    assert plate.amount_paid > 0

    # Activity log recorded vision_checkout
    logs = (await db_session.execute(
        select(ParkingActivityLog).where(
            ParkingActivityLog.tenant_id == tenant_id,
            ParkingActivityLog.event_type == "vision_checkout"
        )
    )).scalars().all()
    assert len(logs) == 1
    assert logs[0].space_id == "P-10"


@pytest.mark.asyncio
async def test_val10_step_11_remap_safety_409(client_a, db_session, admin_val10):
    # Create camera
    cam_data = {"name": "Remap Safety Cam", "location": "Lot B", "stream_url": "rtsp://test", "role": "parking", "is_active": True}
    res_cam = await client_a.post("/api/v1/cameras/", json=cam_data)
    assert res_cam.status_code in (200, 201), res_cam.text
    cam_id = res_cam.json()["id"]

    # Save slots P-01
    slots_payload = [{"space_id": "P-01", "polygon": [[0.1, 0.1], [0.2, 0.1], [0.2, 0.2], [0.1, 0.2]]}]
    await client_a.put(f"/api/v1/parking/cameras/{cam_id}/slots", json={"slots": slots_payload})

    # Mark P-01 as occupied with a vehicle
    space = (await db_session.execute(
        select(ParkingSpace).where(ParkingSpace.tenant_id == admin_val10.tenant_id, ParkingSpace.space_id == "P-01")
    )).scalar_one()
    space.is_occupied = True
    space.vehicle_id = "some-vehicle-id"
    space.entry_time = datetime.now(timezone.utc) - timedelta(minutes=45)
    await db_session.commit()

    # Attempt to re-map slots on cam_id -> 409 Conflict naming P-01
    new_payload = [{"space_id": "P-01", "polygon": [[0.1, 0.1], [0.25, 0.1], [0.25, 0.2], [0.1, 0.2]]}]
    res_remap = await client_a.put(f"/api/v1/parking/cameras/{cam_id}/slots", json={"slots": new_payload})
    assert res_remap.status_code == 409
    assert "P-01" in res_remap.json()["detail"]

    # Space P-01 is still present and occupied with original entry_time
    await db_session.refresh(space)
    assert space.is_occupied is True
    assert space.vehicle_id == "some-vehicle-id"
    assert space.entry_time is not None
