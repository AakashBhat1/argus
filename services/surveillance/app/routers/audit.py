"""GET /api/v1/audit: the admin audit log, scoped to the admin's tenant."""

from argus_common.audit_api import build_audit_router
from app.database import get_db
from app.models import AuditEvent
from app.services.auth import require_admin

router = build_audit_router(model=AuditEvent, get_db=get_db, require_admin=require_admin, path="/audit")
