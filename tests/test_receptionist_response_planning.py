from __future__ import annotations

from dataclasses import fields

import pytest
from pydantic import ValidationError

from app.domain.conversations.enums import ConversationChannel
from app.domain.receptionist.enums import ReceptionistResponseSafetyLevel, ReceptionistResponseType
from app.domain.receptionist.response_planning import (
    FORBIDDEN_PROVIDER_PAYLOAD_FIELDS,
    GeneratedResponse,
    ResponsePlan,
    ResponsePlanCriticalSafetyRequirementError,
    ResponsePlanMissingFallbackTextError,
    build_generated_response,
    build_response_plan,
    sanitize_response_plan_facts,
    sanitize_response_plan_metadata,
    validate_response_plan,
)
from app.schemas.receptionist_response_planning import GeneratedResponseSchema, ResponsePlanSchema

_BLOCKED_FACT_KEYS = frozenset(
    {
        "api_key",
        "raw_payload",
        "raw_provider_output",
        "transcript",
    },
)


def test_valid_response_plan() -> None:
    plan = build_response_plan(
        response_type=ReceptionistResponseType.SCHEDULING,
        channel=ConversationChannel.CHAT,
        fallback_text="I can help you find an appointment.",
        safety_level=ReceptionistResponseSafetyLevel.STANDARD,
        facts={"specialty_name": "Dermatology", "requested_date": "2026-07-15"},
        metadata={"plan_version": "v1"},
    )

    assert plan.response_type == ReceptionistResponseType.SCHEDULING
    assert plan.channel == ConversationChannel.CHAT
    assert plan.fallback_text == "I can help you find an appointment."
    assert plan.safety_level == ReceptionistResponseSafetyLevel.STANDARD
    assert plan.facts == {
        "specialty_name": "Dermatology",
        "requested_date": "2026-07-15",
    }
    assert plan.metadata == {"plan_version": "v1"}
    validate_response_plan(plan)

    schema_plan = ResponsePlanSchema(
        response_type=ReceptionistResponseType.INFORMATIONAL,
        channel=ConversationChannel.VOICE,
        fallback_text="Our clinic is open Monday through Friday.",
    ).to_domain()

    assert schema_plan.response_type == ReceptionistResponseType.INFORMATIONAL
    assert schema_plan.channel == ConversationChannel.VOICE


def test_critical_response_plan_requires_deterministic_behavior_or_safety_level() -> None:
    with pytest.raises(ResponsePlanCriticalSafetyRequirementError):
        build_response_plan(
            response_type=ReceptionistResponseType.CRITICAL,
            channel=ConversationChannel.CHAT,
            fallback_text="Please call emergency services immediately.",
        )

    with pytest.raises(ValidationError):
        ResponsePlanSchema(
            response_type=ReceptionistResponseType.CRITICAL,
            channel=ConversationChannel.CHAT,
            fallback_text="Please call emergency services immediately.",
        )

    deterministic_plan = build_response_plan(
        response_type=ReceptionistResponseType.CRITICAL,
        channel=ConversationChannel.CHAT,
        fallback_text="Please call emergency services immediately.",
        deterministic_behavior=True,
    )
    assert deterministic_plan.deterministic_behavior is True

    safety_plan = build_response_plan(
        response_type=ReceptionistResponseType.CRITICAL,
        channel=ConversationChannel.CHAT,
        fallback_text="Please call emergency services immediately.",
        safety_level=ReceptionistResponseSafetyLevel.CRITICAL,
    )
    assert safety_plan.safety_level == ReceptionistResponseSafetyLevel.CRITICAL


def test_fallback_text_required() -> None:
    with pytest.raises(ResponsePlanMissingFallbackTextError):
        build_response_plan(
            response_type=ReceptionistResponseType.FALLBACK,
            channel=ConversationChannel.CHAT,
            fallback_text="   ",
        )

    with pytest.raises(ValidationError):
        ResponsePlanSchema(
            response_type=ReceptionistResponseType.FALLBACK,
            channel=ConversationChannel.CHAT,
            fallback_text="",
        )


def test_facts_are_safe_dict() -> None:
    raw_facts = {
        "specialty_name": "Cardiology",
        "doctor_name": "Dr. Smith",
        "raw_provider_output": {"choices": []},
        "transcript": "hidden transcript",
        "api_key": "secret",
        "unsupported_field": "drop me",
    }

    safe_facts = sanitize_response_plan_facts(raw_facts)
    assert safe_facts == {
        "specialty_name": "Cardiology",
        "doctor_name": "Dr. Smith",
    }

    for blocked_key in _BLOCKED_FACT_KEYS:
        assert blocked_key not in safe_facts

    plan = build_response_plan(
        response_type=ReceptionistResponseType.SCHEDULING,
        channel=ConversationChannel.CHAT,
        fallback_text="I can help with scheduling.",
        facts=raw_facts,
        metadata={
            "plan_version": "v1",
            "raw_payload": {"provider": "hidden"},
        },
    )
    assert plan.facts == safe_facts
    assert plan.metadata == {"plan_version": "v1"}
    assert sanitize_response_plan_metadata({"raw_payload": {"hidden": True}}) == {}


def test_generated_response_has_no_raw_provider_payload_fields() -> None:
    response_field_names = {field.name for field in fields(GeneratedResponse)}
    plan_field_names = {field.name for field in fields(ResponsePlan)}

    for forbidden_field in FORBIDDEN_PROVIDER_PAYLOAD_FIELDS:
        assert forbidden_field not in response_field_names
        assert forbidden_field not in plan_field_names

    response = build_generated_response(
        text="Your appointment is confirmed.",
        response_type=ReceptionistResponseType.CONFIRMATION,
        channel=ConversationChannel.CHAT,
        used_fallback=False,
        facts={"appointment_id": "apt-123", "raw_provider_output": "strip me"},
        metadata={"generation_source": "deterministic", "provider_output": "strip me"},
    )

    assert response.facts == {"appointment_id": "apt-123"}
    assert response.metadata == {"generation_source": "deterministic"}

    with pytest.raises(ValidationError):
        GeneratedResponseSchema.model_validate(
            {
                "text": "Hello",
                "response_type": ReceptionistResponseType.INFORMATIONAL,
                "channel": ConversationChannel.CHAT,
                "used_fallback": False,
                "raw_provider_output": "forbidden",
            },
        )
