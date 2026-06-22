from __future__ import annotations

from uuid import uuid4

import pytest
from pydantic import ValidationError

from app.domain.retell_tools import (
    ParsedRetellToolCall,
    RetellSupportedToolName,
    parse_retell_tool_call,
)
from app.domain.voice_booking import (
    booking_patient_phone_required,
    is_book_appointment_executable,
)
from app.schemas.retell_tools import (
    MAX_BOOK_APPOINTMENT_CONFIRMATION_TEXT_LENGTH,
    BookAppointmentToolArguments,
    RetellProviderToolCallRequest,
)


def _valid_booking_arguments(
    *,
    hold_id: str | None = None,
    slot_id: str | None = None,
    explicit_confirmation: bool = True,
    patient_phone: str | None = None,
    confirmation_text: str | None = "Yes, please book it.",
) -> dict[str, object]:
    resolved_hold_id = hold_id if hold_id is not None else str(uuid4())
    return {
        "hold_id": resolved_hold_id,
        "slot_id": slot_id,
        "patient_name": "Jane Doe",
        "patient_date_of_birth": "1990-05-15",
        "patient_email": "jane.doe@example.com",
        "patient_phone": patient_phone,
        "explicit_confirmation": explicit_confirmation,
        "confirmation_text": confirmation_text,
        "notes": "Annual checkup",
    }


def test_valid_booking_tool_args_parse() -> None:
    hold_id = str(uuid4())
    slot_id = str(uuid4())

    arguments = BookAppointmentToolArguments.model_validate(
        _valid_booking_arguments(hold_id=hold_id, slot_id=slot_id),
    )

    assert arguments.hold_id == hold_id
    assert str(arguments.slot_id) == slot_id
    assert arguments.patient_name == "Jane Doe"
    assert arguments.patient_email == "jane.doe@example.com"
    assert arguments.explicit_confirmation is True
    assert is_book_appointment_executable(arguments) is True


def test_valid_booking_tool_call_parses_through_retell_adapter() -> None:
    hold_id = str(uuid4())

    request = RetellProviderToolCallRequest.model_validate(
        {
            "provider_call_id": "retell-call-book-1",
            "tool_call_id": "tool-call-book-1",
            "tool_name": "book_appointment",
            "arguments": _valid_booking_arguments(hold_id=hold_id, slot_id=None),
        },
    )

    parsed = parse_retell_tool_call(request)

    assert isinstance(parsed, ParsedRetellToolCall)
    assert parsed.tool_name is RetellSupportedToolName.BOOK_APPOINTMENT
    assert isinstance(parsed.arguments, BookAppointmentToolArguments)
    assert parsed.arguments.hold_id == hold_id


def test_missing_hold_and_slot_rejected() -> None:
    with pytest.raises(ValidationError, match="either hold_id or slot_id is required"):
        BookAppointmentToolArguments.model_validate(
            {
                "patient_name": "Jane Doe",
                "patient_date_of_birth": "1990-05-15",
                "patient_email": "jane.doe@example.com",
                "explicit_confirmation": True,
            },
        )


@pytest.mark.parametrize(
    "missing_field",
    ["patient_name", "patient_date_of_birth", "patient_email"],
)
def test_missing_patient_identity_rejected(missing_field: str) -> None:
    payload = _valid_booking_arguments()
    payload.pop(missing_field)

    with pytest.raises(ValidationError):
        BookAppointmentToolArguments.model_validate(payload)


def test_blank_patient_name_rejected() -> None:
    payload = _valid_booking_arguments()
    payload["patient_name"] = "   "

    with pytest.raises(ValidationError, match="patient_name cannot be blank"):
        BookAppointmentToolArguments.model_validate(payload)


def test_explicit_confirmation_false_is_not_executable() -> None:
    arguments = BookAppointmentToolArguments.model_validate(
        _valid_booking_arguments(explicit_confirmation=False),
    )

    assert arguments.explicit_confirmation is False
    assert is_book_appointment_executable(arguments) is False


def test_invalid_email_rejected() -> None:
    payload = _valid_booking_arguments()
    payload["patient_email"] = "not-an-email"

    with pytest.raises(ValidationError):
        BookAppointmentToolArguments.model_validate(payload)


def test_phone_can_be_omitted_when_shared_booking_rules_allow() -> None:
    assert booking_patient_phone_required() is False

    arguments = BookAppointmentToolArguments.model_validate(
        _valid_booking_arguments(patient_phone=None),
    )

    assert arguments.patient_phone is None
    assert is_book_appointment_executable(arguments) is True


def test_confirmation_text_too_long_rejected() -> None:
    payload = _valid_booking_arguments(
        confirmation_text="x" * (MAX_BOOK_APPOINTMENT_CONFIRMATION_TEXT_LENGTH + 1),
    )

    with pytest.raises(ValidationError):
        BookAppointmentToolArguments.model_validate(payload)
