from __future__ import annotations

import base64
import json
from dataclasses import dataclass
from datetime import datetime
from uuid import UUID


class InvalidAuditLogCursorError(ValueError):
    """Raised when an audit log cursor cannot be decoded."""


@dataclass(frozen=True, slots=True)
class AuditLogCursor:
    created_at: datetime
    id: UUID


def encode_audit_log_cursor(cursor: AuditLogCursor) -> str:
    payload = {
        "created_at": cursor.created_at.isoformat(),
        "id": str(cursor.id),
    }
    raw_payload = json.dumps(payload, separators=(",", ":")).encode("utf-8")

    return base64.urlsafe_b64encode(raw_payload).decode("utf-8")


def decode_audit_log_cursor(raw_cursor: str) -> AuditLogCursor:
    try:
        decoded = base64.urlsafe_b64decode(raw_cursor.encode("utf-8"))
        payload = json.loads(decoded.decode("utf-8"))

        return AuditLogCursor(
            created_at=datetime.fromisoformat(payload["created_at"]),
            id=UUID(payload["id"]),
        )
    except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
        raise InvalidAuditLogCursorError("invalid audit log cursor") from exc
