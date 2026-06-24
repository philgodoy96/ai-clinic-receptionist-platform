from __future__ import annotations

from datetime import date
from types import SimpleNamespace
from uuid import uuid4

import pytest

from app.domain.receptionist.enums import ReceptionistResponseType, ReceptionistTemplateType
from app.domain.retell_tools import RetellSupportedToolName
from app.schemas.retell_tools import BookAppointmentToolArguments
from app.services.receptionist_response_generator import render_deterministic_template
from app.services.retell_tool_adapter import RetellToolCallingAdapter


@pytest.mark.parametrize(
    ("template_type", "facts", "expected"),
    [
        (
            ReceptionistTemplateType.SLOT_HOLD_CREATED,
            {},
            (
                "I can hold that time while I get your details. I'll need your name, "
                "date of birth, and email before I can book it."
            ),
        ),
        (
            ReceptionistTemplateType.BOOKING_SUCCEEDED,
            {
                "appointment_time": "Tuesday, July 2 at 2:00 PM",
                "doctor_name": "Dr. Emily Carter",
            },
            (
                "You're all set. Your appointment is confirmed for Tuesday, July 2 at 2:00 PM "
                "with Dr. Emily Carter. You'll receive a confirmation email shortly. "
                "Is there anything else you need today?"
            ),
        ),
        (
            ReceptionistTemplateType.BOOKING_FAILED,
            {"failure_code": "appointment_hold_expired"},
            (
                "That time may no longer be available. "
                "Let me check the latest schedule again."
            ),
        ),
        (
            ReceptionistTemplateType.BOOKING_FAILED,
            {"failure_code": "patient_not_found"},
            (
                "I'm not matching those details yet — could we try your name "
                "and date of birth once more?"
            ),
        ),
    ],
)
def test_voice_response_templates_use_natural_wording(
    template_type: ReceptionistTemplateType,
    facts: dict[str, str],
    expected: str,
) -> None:
    text = render_deterministic_template(template_type, facts)

    assert text == expected
    assert "slot" not in text.lower()
    assert "hold reference" not in text.lower()


def test_voice_suggested_text_omits_ids_from_booking_result() -> None:
    appointment_id = str(uuid4())
    adapter = RetellToolCallingAdapter(
        scheduling_service=SimpleNamespace(list_doctors=lambda specialty_id=None: []),
        hold_service=SimpleNamespace(),
        voice_calls=SimpleNamespace(),
    )

    result = adapter._build_book_appointment_result(
        SimpleNamespace(
            appointment_id=appointment_id,
            patient_id=str(uuid4()),
            availability_slot_id=str(uuid4()),
            hold_id=str(uuid4()),
            confirmation_email_created=True,
        ),
    )

    suggested = result["suggested_response_text"]
    assert appointment_id not in suggested
    assert "slot" not in suggested.lower()
    assert "you're all set" in suggested.lower()
    assert "is there anything else you need today?" in suggested.lower()


def test_failed_booking_includes_hold_expired_suggested_response_text() -> None:
    from app.domain.retell_tools import ParsedRetellToolCall

    adapter = RetellToolCallingAdapter(
        scheduling_service=SimpleNamespace(),
        hold_service=SimpleNamespace(),
        voice_calls=SimpleNamespace(),
    )
    parsed = ParsedRetellToolCall(
        tool_name=RetellSupportedToolName.BOOK_APPOINTMENT,
        provider_call_id="call-123",
        tool_call_id="tool-call-123",
        arguments=BookAppointmentToolArguments(
            hold_id="hold-123",
            patient_name="Jane Doe",
            patient_date_of_birth=date(1990, 1, 1),
            patient_email="jane@example.test",
            explicit_confirmation=True,
        ),
        occurred_at=None,
    )

    response = adapter._build_failed_with_suggested_response(
        parsed,
        error_code="appointment_hold_expired",
        template_type=ReceptionistTemplateType.BOOKING_FAILED,
        facts={"failure_code": "appointment_hold_expired"},
        fallback_text=(
            "That time may no longer be available. "
            "Let me check the latest schedule again."
        ),
        response_type=ReceptionistResponseType.CONFIRMATION,
    )

    suggested = response.result["suggested_response_text"]
    assert "latest schedule" in suggested.lower()
    assert "slot" not in suggested.lower()
    assert response.error_code == "appointment_hold_expired"
