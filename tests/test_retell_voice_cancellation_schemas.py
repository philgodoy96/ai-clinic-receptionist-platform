from __future__ import annotations

from uuid import uuid4

import pytest
from pydantic import ValidationError

from app.domain.retell_tools import (
    ParsedRetellToolCall,
    RetellSupportedToolName,
    parse_retell_tool_call,
)
from app.domain.voice_cancellation import (
    cancellation_patient_phone_required,
    is_cancel_appointment_executable,
    is_cancel_appointment_reference_ambiguous,
    resolve_cancel_appointment_id,
    validate_cancel_appointment_conversation_context,
)
from app.schemas.retell_tools import (
    MAX_CANCEL_APPOINTMENT_CANCELLATION_REASON_LENGTH,
    MAX_CANCEL_APPOINTMENT_CONFIRMATION_TEXT_LENGTH,
    CancelAppointmentToolArguments,
    RetellProviderToolCallRequest,
)


def _valid_cancellation_arguments(
    *,
    appointment_id: str | None = None,
    explicit_confirmation: bool = True,
    confirmation_text: str | None = "Yes, please cancel it.",
    cancellation_reason: str | None = "Patient requested cancellation",
    patient_name: str | None = "Jane Doe",
    patient_date_of_birth: str | None = "1990-05-15",
    patient_email: str | None = "jane.doe@example.com",
) -> dict[str, object]:
    return {
        "appointment_id": appointment_id if appointment_id is not None else str(uuid4()),
        "explicit_confirmation": explicit_confirmation,
        "confirmation_text": confirmation_text,
        "cancellation_reason": cancellation_reason,
        "patient_name": patient_name,
        "patient_date_of_birth": patient_date_of_birth,
        "patient_email": patient_email,
    }


def test_valid_cancellation_args_parse() -> None:
    appointment_id = str(uuid4())

    arguments = CancelAppointmentToolArguments.model_validate(
        _valid_cancellation_arguments(appointment_id=appointment_id),
    )

    assert str(arguments.appointment_id) == appointment_id
    assert arguments.explicit_confirmation is True
    assert arguments.confirmation_text == "Yes, please cancel it."
    assert arguments.cancellation_reason == "Patient requested cancellation"
    assert arguments.patient_name == "Jane Doe"
    assert arguments.patient_email == "jane.doe@example.com"
    assert is_cancel_appointment_executable(arguments) is True


def test_valid_cancellation_tool_call_parses_through_retell_adapter() -> None:
    appointment_id = str(uuid4())

    request = RetellProviderToolCallRequest.model_validate(
        {
            "provider_call_id": "retell-call-cancel-1",
            "tool_call_id": "tool-call-cancel-1",
            "tool_name": "cancel_appointment",
            "arguments": _valid_cancellation_arguments(appointment_id=appointment_id),
        },
    )

    parsed = parse_retell_tool_call(request)

    assert isinstance(parsed, ParsedRetellToolCall)
    assert parsed.tool_name is RetellSupportedToolName.CANCEL_APPOINTMENT
    assert isinstance(parsed.arguments, CancelAppointmentToolArguments)
    assert str(parsed.arguments.appointment_id) == appointment_id


def test_explicit_confirmation_false_is_not_executable() -> None:
    arguments = CancelAppointmentToolArguments.model_validate(
        _valid_cancellation_arguments(explicit_confirmation=False),
    )

    assert arguments.explicit_confirmation is False
    assert is_cancel_appointment_executable(arguments) is False


def test_confirmation_text_too_long_rejected() -> None:
    payload = _valid_cancellation_arguments(
        confirmation_text="x" * (MAX_CANCEL_APPOINTMENT_CONFIRMATION_TEXT_LENGTH + 1),
    )

    with pytest.raises(ValidationError):
        CancelAppointmentToolArguments.model_validate(payload)


def test_cancellation_reason_too_long_rejected() -> None:
    payload = _valid_cancellation_arguments(
        cancellation_reason="x" * (MAX_CANCEL_APPOINTMENT_CANCELLATION_REASON_LENGTH + 1),
    )

    with pytest.raises(ValidationError):
        CancelAppointmentToolArguments.model_validate(payload)


def test_appointment_id_optional_when_voice_context_provides_reference() -> None:
    appointment_id = uuid4()
    arguments = CancelAppointmentToolArguments.model_validate(
        {
            "explicit_confirmation": True,
            "confirmation_text": "Yes, cancel it.",
        },
    )

    voice_context = {"appointment_id": str(appointment_id)}

    assert resolve_cancel_appointment_id(arguments, voice_context) == appointment_id
    assert is_cancel_appointment_executable(arguments, voice_context=voice_context) is True


def test_missing_appointment_reference_is_not_executable_without_context() -> None:
    arguments = CancelAppointmentToolArguments.model_validate(
        {
            "explicit_confirmation": True,
            "confirmation_text": "Yes, cancel it.",
        },
    )

    assert is_cancel_appointment_executable(arguments) is False


def test_no_phone_number_required() -> None:
    assert cancellation_patient_phone_required() is False

    arguments = CancelAppointmentToolArguments.model_validate(
        _valid_cancellation_arguments(),
    )

    assert not hasattr(arguments, "patient_phone")
    assert is_cancel_appointment_executable(arguments) is True


def test_blocked_transcript_fields_rejected() -> None:
    payload = _valid_cancellation_arguments()
    payload["transcript"] = "Patient said yes please cancel"

    with pytest.raises(ValidationError):
        CancelAppointmentToolArguments.model_validate(payload)


def test_conversation_appointment_id_fallback_when_args_omit_id() -> None:
    appointment_id = uuid4()
    arguments = CancelAppointmentToolArguments.model_validate(
        {
            "explicit_confirmation": True,
            "confirmation_text": "Yes, cancel it.",
        },
    )

    assert resolve_cancel_appointment_id(
        arguments,
        {},
        conversation_appointment_id=appointment_id,
    ) == appointment_id
    assert is_cancel_appointment_executable(
        arguments,
        voice_context={},
        conversation_appointment_id=appointment_id,
    ) is True


def test_ambiguous_appointment_reference_is_not_executable() -> None:
    voice_appointment_id = uuid4()
    conversation_appointment_id = uuid4()
    arguments = CancelAppointmentToolArguments.model_validate(
        {
            "explicit_confirmation": True,
            "confirmation_text": "Yes, cancel it.",
        },
    )

    assert is_cancel_appointment_reference_ambiguous(
        arguments,
        {"appointment_id": str(voice_appointment_id)},
        conversation_appointment_id=conversation_appointment_id,
    ) is True
    assert is_cancel_appointment_executable(
        arguments,
        voice_context={"appointment_id": str(voice_appointment_id)},
        conversation_appointment_id=conversation_appointment_id,
    ) is False


def test_appointment_context_mismatch_is_rejected() -> None:
    argument_appointment_id = uuid4()
    context_appointment_id = uuid4()
    arguments = CancelAppointmentToolArguments.model_validate(
        {
            "appointment_id": str(argument_appointment_id),
            "explicit_confirmation": True,
            "confirmation_text": "Yes, cancel it.",
        },
    )

    assert validate_cancel_appointment_conversation_context(
        argument_appointment_id,
        arguments=arguments,
        voice_context={"appointment_id": str(context_appointment_id)},
        conversation_appointment_id=None,
    ) is False
