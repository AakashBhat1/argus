"""Endpoint to list available video files for demo/testing playback."""

import os
from pathlib import Path

from fastapi import APIRouter, Depends

from app.models import User
from app.services.auth import get_current_active_user

router = APIRouter(prefix="/videos", tags=["videos"])

# Video folder relative to the yolo_classifier package root
_VIDEO_DIR = Path(__file__).resolve().parents[2] / "video"

_ALLOWED_EXTENSIONS = {".mp4", ".avi", ".mkv", ".mov", ".webm", ".flv", ".wmv", ".m4v"}


@router.get("/")
async def list_videos(current_user: User = Depends(get_current_active_user)):
    """Return list of video files available in the video folder."""
    if not _VIDEO_DIR.is_dir():
        return {"videos": [], "video_dir": str(_VIDEO_DIR)}

    videos = []
    for entry in sorted(_VIDEO_DIR.iterdir()):
        if entry.is_file() and entry.suffix.lower() in _ALLOWED_EXTENSIONS:
            stat = entry.stat()
            videos.append({
                "filename": entry.name,
                "path": f"video://{entry.name}",
                "size_mb": round(stat.st_size / (1024 * 1024), 2),
                "extension": entry.suffix.lower(),
            })

    return {"videos": videos, "video_dir": str(_VIDEO_DIR)}
