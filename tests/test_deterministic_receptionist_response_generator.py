from __future__ import annotations

import pytest

from app.domain.conversations.enums import ConversationChannel
from app.domain.receptionist.enums import (
    ReceptionistResponseMode,
    ReceptionistResponseType,
    ReceptionistTemplateType,
)
from app.domain.receptionist.response_planning import build_response_plan
from app.services.receptionist_response_generator import (
    MAX_DETERMINISTIC_RESPONSE_TEXT_LENGTH,
    DeterministicReceptionistResponseGenerator,
    bound_response_text,
)

_PROVIDER_METADATA_KEYS = frozenset(
    {
        "choices",
        "completion",
        "model_response",
        "prompt",
        "provider_output",
        "raw_payload",
        "raw_prompt",
        "raw_provider_output",
        "system_prompt",
    },
)

_CORE_TEMPLATE_FACTS: dict[ReceptionistTemplateType, dict[str, object]] = {
    ReceptionistTemplateType.GREETING: {"template_type": "greeting"},
    ReceptionistTemplateType.ASK_FOR_SPECIALTY: {"template_type": "ask_for_specialty"},
    ReceptionistTemplateType.ASK_FOR_DATE: {
        "template_type": "ask_for_date",
        "specialty_name": "Dermatology",
    },
    ReceptionistTemplateType.ASK_FOR_TIME_PREFERENCE: {
        "template_type": "ask_for_time_preference",
        "requested_date": "2026-07-15",
    },
    ReceptionistTemplateType.AVAILABILITY_OPTIONS: {
        "template_type": "availability_options",
        "requested_date": "2026-07-15",
        "offered_slot_count": 2,
        "time_window_label": "morning",
    },
    ReceptionistTemplateType.SLOT_HOLD_CREATED: {
        "template_type": "slot_hold_created",
        "hold_id": "hold-123",
        "doctor_name": "Dr. Smith",
    },
    ReceptionistTemplateType.ASK_FOR_PATIENT_IDENTITY: {
        "template_type": "ask_for_patient_identity",
    },
    ReceptionistTemplateType.ASK_FOR_CONFIRMATION: {
        "template_type": "ask_for_confirmation",
    },
    ReceptionistTemplateType.BOOKING_SUCCEEDED: {
        "template_type": "booking_succeeded",
        "appointment_id": "apt-456",
    },
    ReceptionistTemplateType.BOOKING_FAILED: {
        "template_type": "booking_failed",
        "failure_reason": "The slot is no longer available.",
    },
    ReceptionistTemplateType.CANCELLATION_SUCCEEDED: {
        "template_type": "cancellation_succeeded",
        "appointment_id": "apt-789",
    },
    ReceptionistTemplateType.CANCELLATION_FAILED: {
        "template_type": "cancellation_failed",
        "failure_reason": "Appointment not found.",
    },
    ReceptionistTemplateType.RESCHEDULE_SUCCEEDED: {
        "template_type": "reschedule_succeeded",
        "appointment_id": "apt-321",
    },
    ReceptionistTemplateType.RESCHEDULE_FAILED: {
        "template_type": "reschedule_failed",
        "failure_reason": "The selected slot is unavailable.",
    },
    ReceptionistTemplateType.EMERGENCY_GUIDANCE: {
        "template_type": "emergency_guidance",
    },
    ReceptionistTemplateType.HUMAN_ESCALATION: {
        "template_type": "human_escalation",
    },
    ReceptionistTemplateType.UNSUPPORTED_REQUEST: {
        "template_type": "unsupported_request",
    },
    ReceptionistTemplateType.GENERIC_ERROR: {
        "template_type": "generic_error",
    },
}


@pytest.mark.parametrize("template_type", list(ReceptionistTemplateType))
def test_each_core_response_type_renders(template_type: ReceptionistTemplateType) -> None:
    generator = DeterministicReceptionistResponseGenerator()
    plan = build_response_plan(
        response_type=_response_type_for_template(template_type),
        channel=ConversationChannel.CHAT,
        fallback_text="Fallback response.",
        facts=_CORE_TEMPLATE_FACTS[template_type],
        deterministic_behavior=template_type == ReceptionistTemplateType.EMERGENCY_GUIDANCE,
        safety_level=None,
    )

    response = generator.generate(plan)

    assert response.text
    assert response.used_fallback is False
    assert response.mode == ReceptionistResponseMode.DETERMINISTIC
    assert response.metadata.get("template_type") == template_type.value


def test_emergency_response_is_deterministic() -> None:
    generator = DeterministicReceptionistResponseGenerator()
    plan = build_response_plan(
        response_type=ReceptionistResponseType.CRITICAL,
        channel=ConversationChannel.CHAT,
        fallback_text="Emergency fallback.",
        facts={"template_type": "emergency_guidance"},
        deterministic_behavior=True,
    )

    response = generator.generate(plan)

    assert response.mode == ReceptionistResponseMode.DETERMINISTIC
    assert response.used_fallback is False
    assert "emergency" in response.text.lower()


def test_unknown_type_uses_fallback_text() -> None:
    generator = DeterministicReceptionistResponseGenerator()
    fallback_text = "Please tell me how I can help with scheduling."
    plan = build_response_plan(
        response_type=ReceptionistResponseType.FALLBACK,
        channel=ConversationChannel.CHAT,
        fallback_text=fallback_text,
        facts={"template_type": "not_a_real_template"},
    )

    response = generator.generate(plan)

    assert response.text == fallback_text
    assert response.used_fallback is True
    assert response.mode == ReceptionistResponseMode.DETERMINISTIC


def test_incomplete_facts_use_fallback_text() -> None:
    generator = DeterministicReceptionistResponseGenerator()
    fallback_text = "I need more details before I can continue."
    plan = build_response_plan(
        response_type=ReceptionistResponseType.SCHEDULING,
        channel=ConversationChannel.CHAT,
        fallback_text=fallback_text,
        facts={"template_type": "booking_succeeded"},
    )

    response = generator.generate(plan)

    assert response.text == fallback_text
    assert response.used_fallback is True


def test_no_provider_metadata_in_deterministic_mode() -> None:
    generator = DeterministicReceptionistResponseGenerator()
    plan = build_response_plan(
        response_type=ReceptionistResponseType.INFORMATIONAL,
        channel=ConversationChannel.CHAT,
        fallback_text="Fallback response.",
        facts={"template_type": "greeting"},
        metadata={
            "generation_source": "deterministic",
            "raw_provider_output": "hidden",
            "provider_output": "hidden",
        },
    )

    response = generator.generate(plan)

    assert response.mode == ReceptionistResponseMode.DETERMINISTIC
    for key in response.metadata:
        assert key not in _PROVIDER_METADATA_KEYS
    for key in response.facts:
        assert key not in _PROVIDER_METADATA_KEYS


def test_output_is_bounded_and_non_empty() -> None:
    generator = DeterministicReceptionistResponseGenerator()
    plan = build_response_plan(
        response_type=ReceptionistResponseType.INFORMATIONAL,
        channel=ConversationChannel.CHAT,
        fallback_text="Short fallback.",
        facts={"template_type": "greeting"},
    )

    response = generator.generate(plan)

    assert response.text
    assert len(response.text) <= MAX_DETERMINISTIC_RESPONSE_TEXT_LENGTH

    oversized = "x" * (MAX_DETERMINISTIC_RESPONSE_TEXT_LENGTH + 50)
    bounded = bound_response_text(oversized)
    assert bounded.endswith("...")
    assert len(bounded) == MAX_DETERMINISTIC_RESPONSE_TEXT_LENGTH


def _response_type_for_template(
    template_type: ReceptionistTemplateType,
) -> ReceptionistResponseType:
    if template_type == ReceptionistTemplateType.EMERGENCY_GUIDANCE:
        return ReceptionistResponseType.CRITICAL
    if template_type == ReceptionistTemplateType.HUMAN_ESCALATION:
        return ReceptionistResponseType.ESCALATION
    if template_type in {
        ReceptionistTemplateType.UNSUPPORTED_REQUEST,
        ReceptionistTemplateType.GENERIC_ERROR,
    }:
        return ReceptionistResponseType.FALLBACK
    if template_type in {
        ReceptionistTemplateType.ASK_FOR_CONFIRMATION,
        ReceptionistTemplateType.BOOKING_SUCCEEDED,
        ReceptionistTemplateType.BOOKING_FAILED,
    }:
        return ReceptionistResponseType.CONFIRMATION
    return ReceptionistResponseType.SCHEDULING
