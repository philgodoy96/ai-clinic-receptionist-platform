from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime, time
from typing import Any
from uuid import UUID

from app.domain.conversations.enums import ConversationChannel
from app.domain.voice_calls.enums import VoiceCallStatus
from app.schemas.retell_tools import CheckAvailabilityToolArguments

_BLOCKED_CONTEXT_KEYS = frozenset(
    {
        "api_key",
        "audio",
        "email",
        "from_number",
        "phone",
        "phone_number",
        "raw_payload",
        "recording",
        "recording_url",
        "to_number",
        "transcript",
        "webhook_secret",
    },
)

_SAFE_VOICE_CONTEXT_KEYS = frozenset(
    {
        "appointment_id",
        "appointment_status",
        "availability_slot_id",
        "doctor_id",
        "doctor_name",
        "end_time",
        "hold_id",
        "requested_date",
        "requested_time_window",
        "rescheduled_from_appointment_id",
        "selected_availability_slot_id",
        "specialty_name",
        "start_time",
    },
)

_SAFE_RESCHEDULE_SUMMARY_KEYS = frozenset(
    {
        "appointment_status",
        "availability_slot_id",
        "duplicate",
        "end_time",
        "failure_code",
        "new_appointment_id",
        "original_appointment_id",
        "start_time",
        "status",
    },
)

_ACTIVE_HOLD_CONTEXT_KEYS = frozenset(
    {
        "availability_slot_id",
        "end_time",
        "hold_id",
        "start_time",
    },
)


class VoiceCallNotFoundForBridgeError(LookupError):
    """Raised when a voice call must exist before bridging to a conversation."""


class ConversationNotFoundForBridgeError(LookupError):
    """Raised when a linked or requested conversation record is missing."""


class VoiceConversationLinkConflictError(ValueError):
    """Raised when relinking a voice call to a different conversation is attempted."""


@dataclass(frozen=True, slots=True)
class ActiveHoldSummary:
    hold_id: str
    availability_slot_id: str | None = None
    doctor_id: str | None = None
    start_time: str | None = None
    end_time: str | None = None


@dataclass(frozen=True, slots=True)
class SchedulingPreferenceSummary:
    requested_date: str | None = None
    requested_time_window: dict[str, str] | None = None
    selected_availability_slot_id: str | None = None


@dataclass(frozen=True, slots=True)
class RescheduleSummary:
    status: str
    original_appointment_id: str | None = None
    new_appointment_id: str | None = None
    appointment_status: str | None = None
    availability_slot_id: str | None = None
    start_time: str | None = None
    end_time: str | None = None
    failure_code: str | None = None
    duplicate: bool | None = None


@dataclass(frozen=True, slots=True)
class VoiceConversationContext:
    provider_call_id: str
    voice_call_id: UUID
    conversation_id: UUID | None
    call_status: VoiceCallStatus
    channel: ConversationChannel | None
    active_hold: ActiveHoldSummary | None = None
    scheduling_preference: SchedulingPreferenceSummary | None = None


@dataclass(frozen=True, slots=True)
class VoiceConversationDebugContext:
    voice_call_id: UUID
    provider: str
    provider_call_id: str
    call_status: VoiceCallStatus
    conversation_id: UUID | None
    conversation_channel: ConversationChannel | None
    active_hold: ActiveHoldSummary | None = None
    requested_specialty: str | None = None
    requested_date: str | None = None
    requested_time_window: dict[str, str] | None = None
    last_selected_slot_id: str | None = None
    last_reschedule_summary: RescheduleSummary | None = None


def _safe_string(value: Any) -> str | None:
    if value is None:
        return None

    if isinstance(value, str):
        normalized = value.strip()
        return normalized or None

    return str(value)


def _safe_time_window(value: Any) -> dict[str, str] | None:
    if not isinstance(value, dict):
        return None

    safe_window: dict[str, str] = {}
    for key in ("label", "start", "end"):
        normalized = _safe_string(value.get(key))
        if normalized is not None:
            safe_window[key] = normalized

    return safe_window or None


def _voice_context_from_metadata(
    conversation_metadata: dict[str, Any],
) -> dict[str, Any]:
    voice_context = conversation_metadata.get("voice_context")
    if not isinstance(voice_context, dict):
        return {}

    safe_context: dict[str, Any] = {}
    for key, value in voice_context.items():
        normalized_key = key.strip().lower()
        if normalized_key in _BLOCKED_CONTEXT_KEYS:
            continue
        if normalized_key not in _SAFE_VOICE_CONTEXT_KEYS:
            continue
        safe_context[normalized_key] = value

    return safe_context


def extract_scheduling_summaries(
    conversation_metadata: dict[str, Any],
) -> tuple[ActiveHoldSummary | None, SchedulingPreferenceSummary | None]:
    voice_context = _voice_context_from_metadata(conversation_metadata)

    active_hold: ActiveHoldSummary | None = None
    hold_id = _safe_string(voice_context.get("hold_id"))
    if hold_id and not _safe_string(voice_context.get("appointment_id")):
        active_hold = ActiveHoldSummary(
            hold_id=hold_id,
            availability_slot_id=_safe_string(voice_context.get("availability_slot_id")),
            doctor_id=_safe_string(voice_context.get("doctor_id")),
            start_time=_safe_string(voice_context.get("start_time")),
            end_time=_safe_string(voice_context.get("end_time")),
        )

    scheduling_preference: SchedulingPreferenceSummary | None = None
    requested_date = _safe_string(voice_context.get("requested_date"))
    requested_time_window = _safe_time_window(voice_context.get("requested_time_window"))
    selected_slot_id = _safe_string(voice_context.get("selected_availability_slot_id"))
    if (
        requested_date is not None
        or requested_time_window is not None
        or selected_slot_id is not None
    ):
        scheduling_preference = SchedulingPreferenceSummary(
            requested_date=requested_date,
            requested_time_window=requested_time_window,
            selected_availability_slot_id=selected_slot_id,
        )

    return active_hold, scheduling_preference


def read_voice_context(conversation_metadata: dict[str, Any]) -> dict[str, Any]:
    return _voice_context_from_metadata(conversation_metadata)


def merge_voice_context_metadata(
    conversation_metadata: dict[str, Any],
    voice_context_updates: dict[str, Any],
) -> dict[str, Any]:
    existing_context = read_voice_context(conversation_metadata)
    safe_updates = {
        key: value
        for key, value in voice_context_updates.items()
        if key.strip().lower() in _SAFE_VOICE_CONTEXT_KEYS
        and key.strip().lower() not in _BLOCKED_CONTEXT_KEYS
    }

    return {
        **conversation_metadata,
        "voice_context": {
            **existing_context,
            **safe_updates,
        },
    }


def clear_active_hold_voice_context_metadata(
    conversation_metadata: dict[str, Any],
) -> dict[str, Any]:
    existing_context = read_voice_context(conversation_metadata)
    cleared_context = {
        key: value
        for key, value in existing_context.items()
        if key not in _ACTIVE_HOLD_CONTEXT_KEYS
    }

    return {
        **conversation_metadata,
        "voice_context": cleared_context,
    }


def _sanitize_reschedule_summary(summary: dict[str, Any]) -> dict[str, Any]:
    return {
        key: value
        for key, value in summary.items()
        if key.strip().lower() in _SAFE_RESCHEDULE_SUMMARY_KEYS
        and key.strip().lower() not in _BLOCKED_CONTEXT_KEYS
    }


def merge_last_reschedule_summary_metadata(
    conversation_metadata: dict[str, Any],
    summary: dict[str, Any],
) -> dict[str, Any]:
    safe_summary = _sanitize_reschedule_summary(summary)
    if not safe_summary:
        return conversation_metadata

    return {
        **conversation_metadata,
        "last_reschedule_summary": safe_summary,
    }


def read_last_reschedule_summary(
    conversation_metadata: dict[str, Any],
) -> RescheduleSummary | None:
    raw_summary = conversation_metadata.get("last_reschedule_summary")
    if not isinstance(raw_summary, dict):
        return None

    safe_summary = _sanitize_reschedule_summary(raw_summary)
    status = _safe_string(safe_summary.get("status"))
    if status is None:
        return None

    duplicate_value = safe_summary.get("duplicate")
    duplicate = duplicate_value if isinstance(duplicate_value, bool) else None

    return RescheduleSummary(
        status=status,
        original_appointment_id=_safe_string(safe_summary.get("original_appointment_id")),
        new_appointment_id=_safe_string(safe_summary.get("new_appointment_id")),
        appointment_status=_safe_string(safe_summary.get("appointment_status")),
        availability_slot_id=_safe_string(safe_summary.get("availability_slot_id")),
        start_time=_safe_string(safe_summary.get("start_time")),
        end_time=_safe_string(safe_summary.get("end_time")),
        failure_code=_safe_string(safe_summary.get("failure_code")),
        duplicate=duplicate,
    )


def _day_bounds_from_requested_date(requested_date: str) -> tuple[datetime, datetime]:
    day = date.fromisoformat(requested_date)
    start = datetime.combine(day, time.min, tzinfo=UTC)
    end = datetime.combine(day, time(23, 59, 59), tzinfo=UTC)

    return start, end


def _apply_requested_time_window(
    *,
    start_from: datetime,
    end_to: datetime,
    requested_time_window: dict[str, str],
) -> tuple[datetime, datetime]:
    start_label = requested_time_window.get("start")
    end_label = requested_time_window.get("end")

    if start_label:
        hour, minute = map(int, start_label.split(":", maxsplit=1))
        start_from = start_from.replace(hour=hour, minute=minute, second=0, microsecond=0)

    if end_label:
        hour, minute = map(int, end_label.split(":", maxsplit=1))
        end_to = start_from.replace(hour=hour, minute=minute, second=59, microsecond=0)

    return start_from, end_to


def resolve_check_availability_arguments(
    arguments: CheckAvailabilityToolArguments,
    voice_context: dict[str, Any],
) -> CheckAvailabilityToolArguments | None:
    start_from = arguments.start_from
    start_to = arguments.start_to
    specialty_name = arguments.specialty_name
    doctor_name = arguments.doctor_name
    doctor_id = arguments.doctor_id

    requested_date = _safe_string(voice_context.get("requested_date"))
    if start_from is None and requested_date is not None:
        start_from, start_to = _day_bounds_from_requested_date(requested_date)

    requested_time_window = _safe_time_window(voice_context.get("requested_time_window"))
    if requested_time_window is not None and start_from is not None and start_to is not None:
        start_from, start_to = _apply_requested_time_window(
            start_from=start_from,
            end_to=start_to,
            requested_time_window=requested_time_window,
        )

    if specialty_name is None:
        specialty_name = _safe_string(voice_context.get("specialty_name"))

    if doctor_name is None:
        doctor_name = _safe_string(voice_context.get("doctor_name"))

    if doctor_id is None:
        doctor_id_value = voice_context.get("doctor_id")
        if doctor_id_value is not None:
            try:
                doctor_id = UUID(str(doctor_id_value))
            except ValueError:
                doctor_id = None

    if start_from is None or start_to is None or start_to <= start_from:
        return None

    return arguments.model_copy(
        update={
            "start_from": start_from,
            "start_to": start_to,
            "specialty_name": specialty_name,
            "doctor_name": doctor_name,
            "doctor_id": doctor_id,
        },
    )
