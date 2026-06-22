from __future__ import annotations

import base64
import json
from dataclasses import dataclass
from datetime import datetime
from uuid import UUID


class InvalidVoiceCallCursorError(ValueError):
    """Raised when a voice call cursor cannot be decoded."""


class InvalidVoiceCallEventCursorError(ValueError):
    """Raised when a voice call event cursor cannot be decoded."""


@dataclass(frozen=True, slots=True)
class VoiceCallCursor:
    created_at: datetime
    id: UUID


@dataclass(frozen=True, slots=True)
class VoiceCallEventCursor:
    occurred_at: datetime
    id: UUID


def encode_voice_call_cursor(cursor: VoiceCallCursor) -> str:
    payload = {
        "created_at": cursor.created_at.isoformat(),
        "id": str(cursor.id),
    }
    raw_payload = json.dumps(payload, separators=(",", ":")).encode("utf-8")

    return base64.urlsafe_b64encode(raw_payload).decode("utf-8")


def decode_voice_call_cursor(raw_cursor: str) -> VoiceCallCursor:
    try:
        decoded = base64.urlsafe_b64decode(raw_cursor.encode("utf-8"))
        payload = json.loads(decoded.decode("utf-8"))

        return VoiceCallCursor(
            created_at=datetime.fromisoformat(payload["created_at"]),
            id=UUID(payload["id"]),
        )
    except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
        raise InvalidVoiceCallCursorError("invalid voice call cursor") from exc


def encode_voice_call_event_cursor(cursor: VoiceCallEventCursor) -> str:
    payload = {
        "occurred_at": cursor.occurred_at.isoformat(),
        "id": str(cursor.id),
    }
    raw_payload = json.dumps(payload, separators=(",", ":")).encode("utf-8")

    return base64.urlsafe_b64encode(raw_payload).decode("utf-8")


def decode_voice_call_event_cursor(raw_cursor: str) -> VoiceCallEventCursor:
    try:
        decoded = base64.urlsafe_b64decode(raw_cursor.encode("utf-8"))
        payload = json.loads(decoded.decode("utf-8"))

        return VoiceCallEventCursor(
            occurred_at=datetime.fromisoformat(payload["occurred_at"]),
            id=UUID(payload["id"]),
        )
    except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
        raise InvalidVoiceCallEventCursorError("invalid voice call event cursor") from exc
