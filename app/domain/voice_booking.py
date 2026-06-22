from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from app.schemas.retell_tools import BookAppointmentToolArguments

_BLOCKED_BOOKING_ARGUMENT_KEYS = frozenset(
    {
        "raw_transcript",
        "transcript",
        "transcript_text",
        "voice_transcript",
    },
)


def booking_patient_phone_required() -> bool:
    return False


def is_book_appointment_executable(arguments: BookAppointmentToolArguments) -> bool:
    if not arguments.explicit_confirmation:
        return False

    if not arguments.patient_name.strip():
        return False

    return True


def contains_blocked_booking_argument_keys(arguments: dict[str, object]) -> bool:
    return any(key in _BLOCKED_BOOKING_ARGUMENT_KEYS for key in arguments)
