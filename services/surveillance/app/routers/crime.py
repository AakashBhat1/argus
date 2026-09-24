from fastapi import APIRouter, Depends

from app.models import User
from app.services.auth import get_current_active_user
from app.services.crime_classifier import crime_classifier

router = APIRouter(prefix="/crime-classifier", tags=["crime-classifier"])


@router.get("/status")
async def crime_classifier_status(current_user: User = Depends(get_current_active_user)):
    """Return ViT crime classifier status, configuration, and usage metrics."""
    return crime_classifier.get_status()
