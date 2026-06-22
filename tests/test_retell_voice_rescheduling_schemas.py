from __future__ import annotations

from uuid import uuid4

import pytest
from pydantic import ValidationError

from app.domain.retell_tools import (
    ParsedRetellToolCall,
    RetellSupportedToolName,
    parse_retell_tool_call,
)
from app.domain.voice_rescheduling import (
    is_reschedule_appointment_executable,
    reschedule_patient_phone_required,
    resolve_reschedule_original_appointment_id,
)
from app.schemas.retell_tools import (
    MAX_RESCHEDULE_APPOINTMENT_CONFIRMATION_TEXT_LENGTH,
    MAX_RESCHEDULE_APPOINTMENT_RESCHEDULE_REASON_LENGTH,
    RescheduleAppointmentToolArguments,
    RetellProviderToolCallRequest,
)


def _valid_reschedule_arguments(
    *,
    original_appointment_id: str | None = None,
    hold_id: str | None = None,
    new_slot_id: str | None = None,
    explicit_confirmation: bool = True,
    confirmation_text: str | None = "Yes, please reschedule it.",
    reschedule_reason: str | None = "Patient requested a new time",
    patient_name: str | None = "Jane Doe",
    patient_date_of_birth: str | None = "1990-05-15",
    patient_email: str | None = "jane.doe@example.com",
) -> dict[str, object]:
    resolved_hold_id = hold_id if hold_id is not None else str(uuid4())
    return {
        "original_appointment_id": original_appointment_id
        if original_appointment_id is not None
        else str(uuid4()),
        "hold_id": resolved_hold_id,
        "new_slot_id": new_slot_id,
        "explicit_confirmation": explicit_confirmation,
        "confirmation_text": confirmation_text,
        "reschedule_reason": reschedule_reason,
        "patient_name": patient_name,
        "patient_date_of_birth": patient_date_of_birth,
        "patient_email": patient_email,
    }


def test_valid_reschedule_args_parse() -> None:
    appointment_id = str(uuid4())
    hold_id = str(uuid4())

    arguments = RescheduleAppointmentToolArguments.model_validate(
        _valid_reschedule_arguments(
            original_appointment_id=appointment_id,
            hold_id=hold_id,
            new_slot_id=None,
        ),
    )

    assert str(arguments.original_appointment_id) == appointment_id
    assert arguments.hold_id == hold_id
    assert arguments.explicit_confirmation is True
    assert arguments.confirmation_text == "Yes, please reschedule it."
    assert arguments.reschedule_reason == "Patient requested a new time"
    assert arguments.patient_name == "Jane Doe"
    assert arguments.patient_email == "jane.doe@example.com"
    assert is_reschedule_appointment_executable(arguments) is True


def test_valid_reschedule_tool_call_parses_through_retell_adapter() -> None:
    appointment_id = str(uuid4())
    hold_id = str(uuid4())

    request = RetellProviderToolCallRequest.model_validate(
        {
            "provider_call_id": "retell-call-reschedule-1",
            "tool_call_id": "tool-call-reschedule-1",
            "tool_name": "reschedule_appointment",
            "arguments": _valid_reschedule_arguments(
                original_appointment_id=appointment_id,
                hold_id=hold_id,
                new_slot_id=None,
            ),
        },
    )

    parsed = parse_retell_tool_call(request)

    assert isinstance(parsed, ParsedRetellToolCall)
    assert parsed.tool_name is RetellSupportedToolName.RESCHEDULE_APPOINTMENT
    assert isinstance(parsed.arguments, RescheduleAppointmentToolArguments)
    assert str(parsed.arguments.original_appointment_id) == appointment_id


def test_explicit_confirmation_false_is_not_executable() -> None:
    arguments = RescheduleAppointmentToolArguments.model_validate(
        _valid_reschedule_arguments(explicit_confirmation=False),
    )

    assert arguments.explicit_confirmation is False
    assert is_reschedule_appointment_executable(arguments) is False


def test_missing_hold_and_new_slot_rejected() -> None:
    with pytest.raises(ValidationError, match="either hold_id or new_slot_id is required"):
        RescheduleAppointmentToolArguments.model_validate(
            {
                "original_appointment_id": str(uuid4()),
                "explicit_confirmation": True,
            },
        )


def test_confirmation_text_too_long_rejected() -> None:
    payload = _valid_reschedule_arguments(
        confirmation_text="x" * (MAX_RESCHEDULE_APPOINTMENT_CONFIRMATION_TEXT_LENGTH + 1),
    )

    with pytest.raises(ValidationError):
        RescheduleAppointmentToolArguments.model_validate(payload)


def test_reschedule_reason_too_long_rejected() -> None:
    payload = _valid_reschedule_arguments(
        reschedule_reason="x" * (MAX_RESCHEDULE_APPOINTMENT_RESCHEDULE_REASON_LENGTH + 1),
    )

    with pytest.raises(ValidationError):
        RescheduleAppointmentToolArguments.model_validate(payload)


def test_original_appointment_id_optional_when_voice_context_provides_reference() -> None:
    appointment_id = uuid4()
    hold_id = str(uuid4())
    arguments = RescheduleAppointmentToolArguments.model_validate(
        {
            "hold_id": hold_id,
            "explicit_confirmation": True,
            "confirmation_text": "Yes, reschedule it.",
        },
    )

    voice_context = {"appointment_id": str(appointment_id)}

    assert resolve_reschedule_original_appointment_id(arguments, voice_context) == appointment_id
    assert is_reschedule_appointment_executable(arguments, voice_context=voice_context) is True


def test_missing_appointment_reference_is_not_executable_without_context() -> None:
    hold_id = str(uuid4())
    arguments = RescheduleAppointmentToolArguments.model_validate(
        {
            "hold_id": hold_id,
            "explicit_confirmation": True,
            "confirmation_text": "Yes, reschedule it.",
        },
    )

    assert is_reschedule_appointment_executable(arguments) is False


def test_no_phone_number_required() -> None:
    assert reschedule_patient_phone_required() is False

    arguments = RescheduleAppointmentToolArguments.model_validate(
        _valid_reschedule_arguments(),
    )

    assert not hasattr(arguments, "patient_phone")
    assert is_reschedule_appointment_executable(arguments) is True


def test_blocked_transcript_fields_rejected() -> None:
    payload = _valid_reschedule_arguments()
    payload["transcript"] = "Patient said yes please reschedule"

    with pytest.raises(ValidationError):
        RescheduleAppointmentToolArguments.model_validate(payload)
