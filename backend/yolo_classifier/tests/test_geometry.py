"""Camera geometry: pinhole distance and ground-plane homography."""

from __future__ import annotations

import math

import pytest

from app.detection.geometry import CameraCalibration, CameraGeometry, ground_distance


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


def test_locate_without_homography_uses_pinhole_ground_estimate():
    geom = CameraGeometry(1280, 720, CameraCalibration(hfov_deg=90.0))
    # Centred person: azimuth 0 -> ground x ~ 0, y ~ range
    bundle = geom.locate("person", (610, 190, 60, 170))
    assert bundle["ground_source"] == "pinhole"
    assert bundle["distance_m"] == pytest.approx(6.4, rel=1e-2)
    assert bundle["ground_x_m"] == pytest.approx(0.0, abs=0.05)
    assert bundle["ground_y_m"] == pytest.approx(6.4, rel=1e-2)
    assert bundle["truncated"] is False


def test_truncated_box_flagged():
    geom = CameraGeometry(1280, 720)
    bundle = geom.locate("person", (600, 560, 60, 160))  # touches bottom edge
    assert bundle["truncated"] is True


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
    a = geom.locate("person", (10, 40, 10, 60))  # foot at (15, 100) -> (1.5, 0)
    b = geom.locate("person", (50, 40, 10, 60))  # foot at (55, 100) -> (5.5, 0)
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
