"""API schemas shared by services that manage cameras."""

from __future__ import annotations

from typing import Optional

from pydantic import BaseModel, Field, field_validator


class CameraCalibrationSchema(BaseModel):
    """Monocular calibration used for distance estimation and ground mapping."""

    hfov_deg: float = Field(default=84.0, ge=10.0, le=170.0)
    # Four or more normalized image points (0..1) and their metric ground
    # coordinates, typically the corners of a parking bay or a measured
    # rectangle on the floor.
    homography_image_points: list[tuple[float, float]] = Field(default_factory=list, max_length=16)
    homography_world_points: list[tuple[float, float]] = Field(default_factory=list, max_length=16)
    class_sizes_m: dict[str, dict[str, float]] = Field(default_factory=dict)
    # Optional measured mounting height and ground position of the camera in
    # the same frame as the world points. When omitted the position is
    # recovered from the calibration (planar PnP).
    camera_height_m: Optional[float] = Field(default=None, gt=0.0, le=200.0)
    camera_ground_position_m: Optional[tuple[float, float]] = None

    @field_validator("homography_image_points")
    @classmethod
    def _validate_image_points(cls, points):
        for x, y in points:
            if not (0.0 <= x <= 1.0 and 0.0 <= y <= 1.0):
                raise ValueError("homography_image_points must be normalized to [0, 1]")
        return points

    @field_validator("homography_world_points")
    @classmethod
    def _validate_world_points(cls, points, info):
        image_points = info.data.get("homography_image_points") or []
        if points and len(points) != len(image_points):
            raise ValueError("homography_world_points must match homography_image_points")
        if points and len(points) < 4:
            raise ValueError("at least four point pairs are required for a ground homography")
        return points
