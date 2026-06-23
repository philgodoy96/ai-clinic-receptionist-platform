from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from app.domain.conversations.enums import ConversationChannel
from app.domain.receptionist.enums import (
    ReceptionistResponseMode,
    ReceptionistResponseSafetyLevel,
    ReceptionistResponseType,
)

_BLOCKED_RESPONSE_PLAN_KEYS = frozenset(
    {
        "api_key",
        "audio",
        "choices",
        "completion",
        "email",
        "from_number",
        "model_response",
        "phone",
        "phone_number",
        "prompt",
        "provider_output",
        "raw_payload",
        "raw_prompt",
        "raw_provider_output",
        "recording",
        "recording_url",
        "system_prompt",
        "to_number",
        "transcript",
        "webhook_secret",
    },
)

_SAFE_FACT_KEYS = frozenset(
    {
        "appointment_id",
        "doctor_name",
        "failure_reason",
        "hold_id",
        "intent",
        "offered_slot_count",
        "offered_slots_summary",
        "requested_date",
        "specialty_name",
        "template_type",
        "time_window_label",
    },
)

_SAFE_METADATA_KEYS = frozenset(
    {
        "deterministic_behavior",
        "estimated_cost_micros",
        "failure_reason",
        "generation_source",
        "input_tokens",
        "mode",
        "model",
        "output_tokens",
        "plan_version",
        "prompt_version",
        "provider",
        "template_type",
        "used_fallback",
    },
)

FORBIDDEN_PROVIDER_PAYLOAD_FIELDS = frozenset(
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


class ResponsePlanValidationError(ValueError):
    """Base exception for response plan validation errors."""


class ResponsePlanMissingResponseTypeError(ResponsePlanValidationError):
    """Raised when response_type is missing or blank."""


class ResponsePlanMissingFallbackTextError(ResponsePlanValidationError):
    """Raised when fallback_text is missing or blank."""


class ResponsePlanCriticalSafetyRequirementError(ResponsePlanValidationError):
    """Raised when a critical response plan lacks safety controls."""


class GeneratedResponseValidationError(ValueError):
    """Base exception for generated response validation errors."""


@dataclass(frozen=True, slots=True)
class ResponsePlan:
    response_type: ReceptionistResponseType
    channel: ConversationChannel
    fallback_text: str
    safety_level: ReceptionistResponseSafetyLevel | None = None
    facts: dict[str, Any] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)
    deterministic_behavior: bool = False


@dataclass(frozen=True, slots=True)
class GeneratedResponse:
    text: str
    response_type: ReceptionistResponseType
    channel: ConversationChannel
    used_fallback: bool
    mode: ReceptionistResponseMode
    safety_level: ReceptionistResponseSafetyLevel | None = None
    facts: dict[str, Any] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)


def _sanitize_mapping(
    values: dict[str, Any],
    *,
    allowed_keys: frozenset[str],
) -> dict[str, Any]:
    safe_values: dict[str, Any] = {}

    for key, value in values.items():
        normalized_key = key.strip().lower()
        if normalized_key in _BLOCKED_RESPONSE_PLAN_KEYS:
            continue
        if normalized_key not in allowed_keys:
            continue
        safe_values[normalized_key] = value

    return safe_values


def sanitize_response_plan_facts(facts: dict[str, Any]) -> dict[str, Any]:
    return _sanitize_mapping(facts, allowed_keys=_SAFE_FACT_KEYS)


def sanitize_response_plan_metadata(metadata: dict[str, Any]) -> dict[str, Any]:
    return _sanitize_mapping(metadata, allowed_keys=_SAFE_METADATA_KEYS)


def validate_response_plan(plan: ResponsePlan) -> None:
    if plan.response_type is None:
        raise ResponsePlanMissingResponseTypeError("response_type is required")

    if not plan.fallback_text.strip():
        raise ResponsePlanMissingFallbackTextError("fallback_text is required")

    if plan.response_type == ReceptionistResponseType.CRITICAL and not (
        plan.deterministic_behavior or plan.safety_level is not None
    ):
        raise ResponsePlanCriticalSafetyRequirementError(
            "critical response plans require deterministic_behavior or safety_level",
        )


def validate_generated_response(response: GeneratedResponse) -> None:
    if not response.text.strip():
        msg = "text is required"
        raise GeneratedResponseValidationError(msg)

    for forbidden_field in FORBIDDEN_PROVIDER_PAYLOAD_FIELDS:
        if hasattr(response, forbidden_field):
            msg = f"generated response must not include provider payload field: {forbidden_field}"
            raise GeneratedResponseValidationError(msg)


def build_response_plan(
    *,
    response_type: ReceptionistResponseType,
    channel: ConversationChannel,
    fallback_text: str,
    safety_level: ReceptionistResponseSafetyLevel | None = None,
    facts: dict[str, Any] | None = None,
    metadata: dict[str, Any] | None = None,
    deterministic_behavior: bool = False,
) -> ResponsePlan:
    plan = ResponsePlan(
        response_type=response_type,
        channel=channel,
        fallback_text=fallback_text,
        safety_level=safety_level,
        facts=sanitize_response_plan_facts(facts or {}),
        metadata=sanitize_response_plan_metadata(metadata or {}),
        deterministic_behavior=deterministic_behavior,
    )
    validate_response_plan(plan)
    return plan


def build_generated_response(
    *,
    text: str,
    response_type: ReceptionistResponseType,
    channel: ConversationChannel,
    used_fallback: bool,
    mode: ReceptionistResponseMode,
    safety_level: ReceptionistResponseSafetyLevel | None = None,
    facts: dict[str, Any] | None = None,
    metadata: dict[str, Any] | None = None,
) -> GeneratedResponse:
    response = GeneratedResponse(
        text=text,
        response_type=response_type,
        channel=channel,
        used_fallback=used_fallback,
        mode=mode,
        safety_level=safety_level,
        facts=sanitize_response_plan_facts(facts or {}),
        metadata=sanitize_response_plan_metadata(metadata or {}),
    )
    validate_generated_response(response)
    return response
