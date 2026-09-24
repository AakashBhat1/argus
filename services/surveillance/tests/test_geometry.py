"""Camera geometry: pinhole distance and ground-plane homography."""

from __future__ import annotations

import math

import pytest

from argus_vision.geometry import CameraCalibration, CameraGeometry, ground_distance


def test_focal_length_from_hfov():
    geom = CameraGeometry(1280, 720, CameraCalibration(hfov_deg=90.0))
    # HFOV 90° -> fx = (W/2) / tan(45°) = W/2
    assert geom.fx == pytest.approx(640.0)
    assert geom.cx == 640.0 and geom.cy == 360.0


def test_person_distance_scales_inversely_with_bbox_height():
    geom = CameraGeometry(1280, 720, CameraCalibration(hfov_deg=90.0))
    # person 1.7 m tall, 170 px high at f=640 -> 6.4 m
    near = geom.estimate_distance("person", 60, 170)
    far = geom.estimate_distance("person", 30, 85)
    assert near == pytest.approx(6.4, rel=1e-3)
    assert far == pytest.approx(12.8, rel=1e-3)


def test_unknown_class_or_degenerate_box_returns_none():
    geom = CameraGeometry()
    assert geom.estimate_distance("unicorn", 10, 10) is None
    assert geom.estimate_distance("person", 10, 0.5) is None


def test_locate_without_homography_does_not_claim_metric_ground_position():
    geom = CameraGeometry(1280, 720, CameraCalibration(hfov_deg=90.0))
    bundle = geom.locate("person", (610, 190, 60, 170))
    assert bundle["ground_source"] is None
    assert bundle["distance_m"] is None
    assert bundle["ground_x_m"] is None
    assert bundle["ground_y_m"] is None
    assert bundle["truncated"] is False


def test_truncated_box_flagged():
    geom = CameraGeometry(1280, 720)
    bundle = geom.locate("person", (600, 560, 60, 160))  # touches bottom edge
    assert bundle["truncated"] is True


@pytest.mark.parametrize(
    "bbox",
    [(-1, 100, 60, 160), (1225, 100, 60, 160)],
)
def test_horizontally_truncated_box_is_flagged_and_range_suppressed(bbox):
    geom = CameraGeometry(1280, 720)
    bundle = geom.locate("person", bbox)
    assert bundle["truncated"] is True
    assert bundle["distance_m"] is None
    assert bundle["ground_x_m"] is None
    assert bundle["ground_y_m"] is None


def test_homography_distance_uses_ground_position_not_bbox_height():
    cal = CameraCalibration(
        homography_image_points=[[0.0, 1.0], [1.0, 1.0], [1.0, 0.0], [0.0, 0.0]],
        homography_world_points=[[0.0, 0.0], [10.0, 0.0], [10.0, 10.0], [0.0, 10.0]],
    )
    geom = CameraGeometry(100, 100, cal)
    upright = geom.locate("person", (45, 20, 10, 60))
    crouched = geom.locate("person", (45, 50, 10, 30))
    assert upright["distance_m"] == pytest.approx(crouched["distance_m"], abs=0.01)


def test_homography_maps_calibration_rectangle_to_metres():
    # A 2.5 m x 5 m parking bay seen in perspective (normalized coords).
    cal = CameraCalibration(
        hfov_deg=84.0,
        homography_image_points=[[0.40, 0.90], [0.60, 0.90], [0.55, 0.60], [0.45, 0.60]],
        homography_world_points=[[0.0, 0.0], [2.5, 0.0], [2.5, 5.0], [0.0, 5.0]],
    )
    geom = CameraGeometry(1000, 1000, cal)
    assert geom.has_ground_plane

    corner = geom.foot_to_ground(600, 900)  # second image point
    assert corner.x_m == pytest.approx(2.5, abs=1e-6)
    assert corner.y_m == pytest.approx(0.0, abs=1e-6)

    mid = geom.foot_to_ground(500, 900)  # halfway along the near edge
    assert mid.x_m == pytest.approx(1.25, abs=1e-6)


def test_ground_distance_between_two_objects_with_homography():
    cal = CameraCalibration(
        homography_image_points=[[0.0, 1.0], [1.0, 1.0], [1.0, 0.0], [0.0, 0.0]],
        homography_world_points=[[0.0, 0.0], [10.0, 0.0], [10.0, 10.0], [0.0, 10.0]],
    )
    geom = CameraGeometry(100, 100, cal)  # identity-ish scale: 10 px = 1 m
    a = geom.locate("person", (10, 40, 10, 57))
    b = geom.locate("person", (50, 40, 10, 57))
    assert a["ground_source"] == "homography"
    assert ground_distance(a, b) == pytest.approx(4.0, abs=1e-6)


def test_update_resolution_recomputes_intrinsics_and_homography():
    cal = CameraCalibration(
        hfov_deg=90.0,
        homography_image_points=[[0.0, 1.0], [1.0, 1.0], [1.0, 0.0], [0.0, 0.0]],
        homography_world_points=[[0.0, 0.0], [10.0, 0.0], [10.0, 10.0], [0.0, 10.0]],
    )
    geom = CameraGeometry(100, 100, cal)
    geom.update_resolution(200, 200)
    assert geom.fx == pytest.approx(100.0)
    pt = geom.foot_to_ground(200, 200)
    assert pt.x_m == pytest.approx(10.0, abs=1e-6)


def test_calibration_from_dict_is_defensive():
    cal = CameraCalibration.from_dict({"hfov_deg": "not-a-number", "homography_image_points": [[0.1]]})
    assert cal.hfov_deg == 84.0
    assert cal.homography_image_points == []
    assert CameraCalibration.from_dict(None).hfov_deg == 84.0
    assert CameraCalibration.from_dict({"hfov_deg": 400}).hfov_deg == 84.0


def test_pixel_to_angles_sign_convention():
    geom = CameraGeometry(1280, 720, CameraCalibration(hfov_deg=90.0))
    az_right, el_up = geom.pixel_to_angles(1280, 0)
    assert az_right == pytest.approx(math.radians(45.0))
    assert el_up > 0


# -- camera pose / camera-relative range --------------------------------------

import numpy as np

from argus_vision.geometry import GroundPoint


def _look_at(camera: np.ndarray, target: np.ndarray) -> np.ndarray:
    """World->camera rotation (OpenCV convention: x right, y down, z forward)."""
    forward = target - camera
    forward = forward / np.linalg.norm(forward)
    up_world = np.array([0.0, 0.0, 1.0])
    right = np.cross(forward, up_world)
    right = right / np.linalg.norm(right)
    down = np.cross(forward, right)
    return np.vstack([right, down, forward])


def _synthetic_calibration(camera_xyz, hfov_deg=70.0, width=1280, height=720, target=(1.25, 2.5, 0.0)):
    camera = np.array(camera_xyz, dtype=float)
    rotation = _look_at(camera, np.array(target, dtype=float))
    focal = (width / 2.0) / math.tan(math.radians(hfov_deg) / 2.0)
    world = [[0.0, 0.0], [2.5, 0.0], [2.5, 5.0], [0.0, 5.0]]

    def project(x, y):
        cam = rotation @ (np.array([x, y, 0.0]) - camera)
        return (focal * cam[0] / cam[2] + width / 2.0, focal * cam[1] / cam[2] + height / 2.0)

    image = [[u / width, v / height] for u, v in (project(x, y) for x, y in world)]
    return world, image, project


def test_range_is_measured_from_camera_not_calibration_origin():
    """Regression for the audit's 'bay-origin fallacy'.

    A person standing on the calibration origin (a bay corner) is 10+ m from
    the camera, not 0 m, and walking toward the camera must shrink the range.
    """
    world, image, project = _synthetic_calibration((3.0, -10.0, 6.0), hfov_deg=70.0)
    cal = CameraCalibration(hfov_deg=70.0, homography_image_points=image, homography_world_points=world)
    geom = CameraGeometry(1280, 720, cal)

    pose = geom.camera_pose
    assert pose is not None and pose.source == "pnp"
    assert pose.ground_x_m == pytest.approx(3.0, abs=0.05)
    assert pose.ground_y_m == pytest.approx(-10.0, abs=0.05)
    assert pose.height_m == pytest.approx(6.0, abs=0.05)

    at_origin = geom.range_from_camera(GroundPoint(0.0, 0.0))
    assert at_origin == pytest.approx(math.hypot(3.0, 10.0), abs=0.05)

    far = geom.range_from_camera(GroundPoint(1.25, 5.0))
    near = geom.range_from_camera(GroundPoint(1.25, 0.0))
    assert near < far


def test_locate_reports_camera_range_for_foot_point():
    world, image, project = _synthetic_calibration((3.0, -10.0, 6.0), hfov_deg=70.0)
    cal = CameraCalibration(hfov_deg=70.0, homography_image_points=image, homography_world_points=world)
    geom = CameraGeometry(1280, 720, cal)
    u, v = project(1.25, 2.5)
    bundle = geom.locate("person", (u - 20, v - 120, 40, 120))
    assert bundle["distance_source"] == "ground_plane"
    assert bundle["ground_x_m"] == pytest.approx(1.25, abs=0.02)
    assert bundle["ground_y_m"] == pytest.approx(2.5, abs=0.02)
    assert bundle["distance_m"] == pytest.approx(math.hypot(1.75, 12.5), abs=0.05)


def test_focal_length_is_self_calibrated_when_hfov_is_wrong():
    world, image, _ = _synthetic_calibration((3.0, -10.0, 6.0), hfov_deg=60.0)
    # Operator left the default 84° HFOV; the true lens is 60°.
    cal = CameraCalibration(hfov_deg=84.0, homography_image_points=image, homography_world_points=world)
    geom = CameraGeometry(1280, 720, cal)
    pose = geom.camera_pose
    assert pose is not None
    true_focal = 640.0 / math.tan(math.radians(30.0))
    assert pose.focal_px == pytest.approx(true_focal, rel=0.01)
    assert pose.ground_y_m == pytest.approx(-10.0, abs=0.1)


def test_measured_camera_position_overrides_recovered_pose():
    world, image, _ = _synthetic_calibration((3.0, -10.0, 6.0), hfov_deg=70.0)
    cal = CameraCalibration.from_dict(
        {
            "hfov_deg": 70.0,
            "homography_image_points": image,
            "homography_world_points": world,
            "camera_ground_position_m": [3.2, -9.8],
            "camera_height_m": 6.1,
        }
    )
    geom = CameraGeometry(1280, 720, cal)
    assert geom.camera_pose.source == "measured"
    assert geom.camera_pose.height_m == pytest.approx(6.1)
    assert geom.range_from_camera(GroundPoint(3.2, -4.8)) == pytest.approx(5.0)


def test_inconsistent_calibration_withholds_range():
    # Image points that no real pinhole camera could produce for this
    # rectangle (a bow-tie); a homography exists, a physical pose does not.
    cal = CameraCalibration(
        hfov_deg=70.0,
        homography_image_points=[[0.2, 0.8], [0.8, 0.2], [0.8, 0.8], [0.2, 0.2]],
        homography_world_points=[[0.0, 0.0], [2.5, 0.0], [2.5, 5.0], [0.0, 5.0]],
    )
    geom = CameraGeometry(1280, 720, cal)
    bundle = geom.locate("person", (600, 300, 40, 120))
    assert geom.camera_pose is None
    assert bundle["distance_m"] is None
    assert bundle["distance_source"] is None


def test_calibration_round_trips_camera_fields():
    cal = CameraCalibration.from_dict(
        {"camera_height_m": "4.5", "camera_ground_position_m": [1, 2]}
    )
    data = cal.to_dict()
    assert data["camera_height_m"] == 4.5
    assert data["camera_ground_position_m"] == [1.0, 2.0]
    assert CameraCalibration.from_dict({"camera_height_m": -3}).camera_height_m is None
    assert CameraCalibration.from_dict({"camera_ground_position_m": ["x"]}).camera_ground_position_m is None
