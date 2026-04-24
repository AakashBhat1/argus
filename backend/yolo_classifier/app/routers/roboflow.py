from fastapi import APIRouter, Depends

from app.models import User
from app.services.auth import get_current_active_user
from app.services.roboflow_classifier import roboflow_classifier

router = APIRouter(prefix="/roboflow", tags=["roboflow"])


@router.get("/status")
async def roboflow_status(current_user: User = Depends(get_current_active_user)):
    """Return Roboflow integration status, configuration, and usage metrics."""
    return roboflow_classifier.get_status()
