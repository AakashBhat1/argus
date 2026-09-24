"""Endpoint to list available video files for demo/testing playback."""

from fastapi import APIRouter, Depends

from app.models import User
from app.services.auth import get_current_active_user
from argus_vision.sources import video_source_roots

router = APIRouter(prefix="/videos", tags=["videos"])

_ALLOWED_EXTENSIONS = {".mp4", ".avi", ".mkv", ".mov", ".webm", ".flv", ".wmv", ".m4v"}


@router.get("/")
async def list_videos(current_user: User = Depends(get_current_active_user)):
    """List video files available as ``video://`` sources.

    Only file names are returned; server filesystem paths are not exposed.
    """
    seen: set[str] = set()
    videos = []
    entries = [
        entry
        for root in video_source_roots()
        if root.is_dir()
        for entry in sorted(root.iterdir())
    ]
    for entry in entries:
        if entry.name in seen:
            continue
        if entry.is_file() and entry.suffix.lower() in _ALLOWED_EXTENSIONS:
            seen.add(entry.name)
            stat = entry.stat()
            videos.append({
                "filename": entry.name,
                "path": f"video://{entry.name}",
                "size_mb": round(stat.st_size / (1024 * 1024), 2),
                "extension": entry.suffix.lower(),
            })

    return {"videos": videos, "video_dir": "video/"}
