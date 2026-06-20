from typing import Protocol

from app.models.audit import AuditLog


class AuditLogRepository(Protocol):
    def add(self, audit_log: AuditLog) -> AuditLog:
        raise NotImplementedError