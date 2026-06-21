from __future__ import annotations

import base64
import json
from dataclasses import dataclass
from datetime import datetime
from uuid import UUID


class InvalidHumanEscalationCursorError(ValueError):
    """Raised when a human escalation cursor cannot be decoded."""


@dataclass(frozen=True, slots=True)
class HumanEscalationCursor:
    created_at: datetime
    id: UUID


def encode_human_escalation_cursor(cursor: HumanEscalationCursor) -> str:
    payload = {
        "created_at": cursor.created_at.isoformat(),
        "id": str(cursor.id),
    }
    raw_payload = json.dumps(payload, separators=(",", ":")).encode("utf-8")

    return base64.urlsafe_b64encode(raw_payload).decode("utf-8")


def decode_human_escalation_cursor(raw_cursor: str) -> HumanEscalationCursor:
    try:
        decoded = base64.urlsafe_b64decode(raw_cursor.encode("utf-8"))
        payload = json.loads(decoded.decode("utf-8"))

        return HumanEscalationCursor(
            created_at=datetime.fromisoformat(payload["created_at"]),
            id=UUID(payload["id"]),
        )
    except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
        raise InvalidHumanEscalationCursorError(
            "invalid human escalation cursor",
        ) from exc
