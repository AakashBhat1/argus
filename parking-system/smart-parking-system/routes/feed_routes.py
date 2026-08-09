"""
Smart Parking System — Video Feed Streaming Routes
"""
from flask import Blueprint, Response
from services import camera_service

feed_bp = Blueprint("feed", __name__, url_prefix="/feed")


@feed_bp.route("/camera")
def camera_feed():
    return Response(
        camera_service.generate_detection_frames(),
        mimetype="multipart/x-mixed-replace; boundary=frame"
    )


@feed_bp.route("/parking")
def parking_feed():
    return Response(
        camera_service.generate_parking_frames(),
        mimetype="multipart/x-mixed-replace; boundary=frame"
    )
