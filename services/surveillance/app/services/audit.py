"""Surveillance's audit helper: ``record`` stages a row in the caller's transaction.

record(db, request, actor, action, target_type=None, target_id=None, details=None)
Call it before the commit of the change it describes. See argus_common.audit.
"""

from argus_common.audit import make_recorder
from app.models import AuditEvent

record = make_recorder(AuditEvent)
