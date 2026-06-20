from __future__ import annotations

import base64
import json
from dataclasses import dataclass
from datetime import datetime
from uuid import UUID


class InvalidEmailJobCursorError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class EmailJobCursor:
    created_at: datetime
    id: UUID


def encode_email_job_cursor(cursor: EmailJobCursor) -> str:
    payload = {
        "created_at": cursor.created_at.isoformat(),
        "id": str(cursor.id),
    }
    raw_payload = json.dumps(payload, separators=(",", ":")).encode("utf-8")

    return base64.urlsafe_b64encode(raw_payload).decode("utf-8")


def decode_email_job_cursor(raw_cursor: str) -> EmailJobCursor:
    try:
        decoded = base64.urlsafe_b64decode(raw_cursor.encode("utf-8"))
        payload = json.loads(decoded.decode("utf-8"))

        return EmailJobCursor(
            created_at=datetime.fromisoformat(payload["created_at"]),
            id=UUID(payload["id"]),
        )
    except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
        raise InvalidEmailJobCursorError("invalid email job cursor") from exc