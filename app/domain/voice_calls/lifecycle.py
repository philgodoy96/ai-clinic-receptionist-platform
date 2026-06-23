from __future__ import annotations

from datetime import datetime
from typing import Any

from app.domain.voice_calls.enums import NormalizedVoiceCallEventType, VoiceCallStatus

_TERMINAL_STATUSES = frozenset(
    {
        VoiceCallStatus.ENDED,
        VoiceCallStatus.FAILED,
    },
)

_RETELL_EVENT_TYPE_MAP: dict[str, NormalizedVoiceCallEventType] = {
    "call_started": NormalizedVoiceCallEventType.CALL_STARTED,
    "started": NormalizedVoiceCallEventType.CALL_STARTED,
    "call_ended": NormalizedVoiceCallEventType.CALL_ENDED,
    "ended": NormalizedVoiceCallEventType.CALL_ENDED,
    "call_failed": NormalizedVoiceCallEventType.CALL_FAILED,
    "failed": NormalizedVoiceCallEventType.CALL_FAILED,
    "call_analyzed": NormalizedVoiceCallEventType.CALL_UPDATED,
    "call_updated": NormalizedVoiceCallEventType.CALL_UPDATED,
    "in_progress": NormalizedVoiceCallEventType.CALL_UPDATED,
}

_SAFE_EVENT_METADATA_KEYS = frozenset(
    {
        "agent_id",
        "agent_version",
        "call_status",
        "call_type",
        "disconnect_reason",
        "disconnection_reason",
        "duration_ms",
        "duration_seconds",
        "end_reason",
    },
)

_BLOCKED_EVENT_METADATA_KEYS = frozenset(
    {
        "api_key",
        "audio",
        "from_number",
        "raw_payload",
        "recording",
        "recording_url",
        "to_number",
        "transcript",
        "webhook_secret",
    },
)


def redact_phone_number(phone_number: str) -> str:
    digits = "".join(character for character in phone_number if character.isdigit())
    if len(digits) < 4:
        return "*" * len(digits) if digits else "****"

    visible_suffix = digits[-4:]
    masked_prefix = "*" * (len(digits) - 4)
    return f"{masked_prefix}{visible_suffix}"


def normalize_retell_event_type(event_type: str) -> NormalizedVoiceCallEventType:
    normalized = event_type.strip().lower()
    return _RETELL_EVENT_TYPE_MAP.get(normalized, NormalizedVoiceCallEventType.UNKNOWN)


def build_voice_call_event_idempotency_key(
    *,
    provider: str,
    provider_call_id: str,
    event_type: str,
    provider_event_id: str | None,
    sequence_number: int | None,
    occurred_at: datetime,
) -> str:
    suffix = provider_event_id
    if suffix is None and sequence_number is not None:
        suffix = str(sequence_number)
    if suffix is None:
        suffix = occurred_at.isoformat()

    return f"{provider}:{provider_call_id}:{event_type}:{suffix}"


def status_for_normalized_event(
    normalized_event_type: NormalizedVoiceCallEventType,
) -> VoiceCallStatus | None:
    if normalized_event_type == NormalizedVoiceCallEventType.CALL_STARTED:
        return VoiceCallStatus.IN_PROGRESS
    if normalized_event_type == NormalizedVoiceCallEventType.CALL_ENDED:
        return VoiceCallStatus.ENDED
    if normalized_event_type == NormalizedVoiceCallEventType.CALL_FAILED:
        return VoiceCallStatus.FAILED
    if normalized_event_type == NormalizedVoiceCallEventType.CALL_UPDATED:
        return VoiceCallStatus.IN_PROGRESS

    return None


def should_apply_status_update(
    *,
    current_status: VoiceCallStatus,
    proposed_status: VoiceCallStatus | None,
    event_occurred_at: datetime,
    previous_last_event_at: datetime | None,
) -> bool:
    if proposed_status is None:
        return False

    if current_status in _TERMINAL_STATUSES:
        return False

    if previous_last_event_at is not None and event_occurred_at < previous_last_event_at:
        return False

    return True


def build_safe_event_metadata(metadata: dict[str, Any]) -> dict[str, Any]:
    safe_metadata: dict[str, Any] = {}

    for key, value in metadata.items():
        normalized_key = key.strip().lower()
        if normalized_key in _BLOCKED_EVENT_METADATA_KEYS:
            continue
        if normalized_key not in _SAFE_EVENT_METADATA_KEYS:
            continue
        safe_metadata[normalized_key] = value

    return safe_metadata
