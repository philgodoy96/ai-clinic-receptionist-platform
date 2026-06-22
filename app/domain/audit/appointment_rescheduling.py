from __future__ import annotations

from typing import Any
from uuid import UUID

_BLOCKED_RESCHEDULE_AUDIT_METADATA_KEYS = frozenset(
    {
        "api_key",
        "audio",
        "email",
        "from_number",
        "phone",
        "phone_number",
        "raw_payload",
        "raw_transcript",
        "recording",
        "recording_url",
        "to_number",
        "transcript",
        "transcript_text",
        "voice_transcript",
        "webhook_secret",
    },
)

_SAFE_RESCHEDULE_AUDIT_METADATA_KEYS = frozenset(
    {
        "already_rescheduled",
        "duplicate",
        "failure_code",
        "hold_id",
        "idempotency_key",
        "new_appointment_id",
        "new_slot_id",
        "original_appointment_id",
        "reason",
    },
)


def build_safe_reschedule_audit_metadata(
    *,
    idempotency_key: str,
    original_appointment_id: UUID | None = None,
    new_appointment_id: UUID | None = None,
    new_slot_id: UUID | None = None,
    hold_id: UUID | None = None,
    failure_code: str | None = None,
    duplicate: bool | None = None,
    already_rescheduled: bool | None = None,
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    raw_metadata: dict[str, Any] = {
        "idempotency_key": idempotency_key,
    }

    if original_appointment_id is not None:
        raw_metadata["original_appointment_id"] = str(original_appointment_id)

    if new_appointment_id is not None:
        raw_metadata["new_appointment_id"] = str(new_appointment_id)

    if new_slot_id is not None:
        raw_metadata["new_slot_id"] = str(new_slot_id)

    if hold_id is not None:
        raw_metadata["hold_id"] = str(hold_id)

    if failure_code is not None:
        raw_metadata["failure_code"] = failure_code

    if duplicate is not None:
        raw_metadata["duplicate"] = duplicate

    if already_rescheduled is not None:
        raw_metadata["already_rescheduled"] = already_rescheduled

    if extra is not None:
        raw_metadata.update(extra)

    return sanitize_reschedule_audit_metadata(raw_metadata)


def sanitize_reschedule_audit_metadata(metadata: dict[str, Any]) -> dict[str, Any]:
    safe_metadata: dict[str, Any] = {}

    for key, value in metadata.items():
        normalized_key = key.strip().lower()
        if normalized_key in _BLOCKED_RESCHEDULE_AUDIT_METADATA_KEYS:
            continue
        if normalized_key not in _SAFE_RESCHEDULE_AUDIT_METADATA_KEYS:
            continue
        safe_metadata[normalized_key] = value

    return safe_metadata
