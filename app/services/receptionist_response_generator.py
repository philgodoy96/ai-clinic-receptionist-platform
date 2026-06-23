from __future__ import annotations

import json
import logging
from typing import Any, Protocol

from app.ai.llm_provider import (
    LLMMessage,
    LLMProvider,
    LLMProviderError,
    LLMProviderName,
    LLMRequest,
)
from app.ai.prompt_versions import get_current_receptionist_response_prompt_metadata
from app.ai.provider_factory import create_llm_provider_from_settings
from app.ai.receptionist_response_output import ReceptionistLLMPhrasedResponse
from app.ai.receptionist_response_prompt import build_receptionist_response_system_prompt
from app.ai.response_output_validator import ResponseOutputValidationError, ResponseOutputValidator
from app.ai.structured_output import (
    StructuredOutputParseError,
    StructuredOutputValidationError,
    parse_structured_output_with_repair_flag,
)
from app.core.config import Settings
from app.domain.receptionist.enums import (
    ReceptionistResponseMode,
    ReceptionistResponseSafetyLevel,
    ReceptionistResponseType,
    ReceptionistTemplateType,
)
from app.domain.receptionist.response_planning import (
    GeneratedResponse,
    ResponsePlan,
    build_generated_response,
)

logger = logging.getLogger("app.receptionist_response_generator")

MAX_DETERMINISTIC_RESPONSE_TEXT_LENGTH = 2000

_REQUIRED_TEMPLATE_FACTS: dict[ReceptionistTemplateType, frozenset[str]] = {
    ReceptionistTemplateType.ASK_FOR_TIME_PREFERENCE: frozenset({"requested_date"}),
    ReceptionistTemplateType.AVAILABILITY_OPTIONS: frozenset(
        {"offered_slot_count", "requested_date"},
    ),
    ReceptionistTemplateType.SLOT_HOLD_CREATED: frozenset({"hold_id"}),
    ReceptionistTemplateType.BOOKING_SUCCEEDED: frozenset({"appointment_id"}),
    ReceptionistTemplateType.CANCELLATION_SUCCEEDED: frozenset({"appointment_id"}),
    ReceptionistTemplateType.RESCHEDULE_SUCCEEDED: frozenset({"appointment_id"}),
}


class ReceptionistResponseGenerator(Protocol):
    def generate(self, plan: ResponsePlan) -> GeneratedResponse: ...


class DeterministicReceptionistResponseGenerator:
    def generate(self, plan: ResponsePlan) -> GeneratedResponse:
        template_type = resolve_template_type(plan.facts)
        if template_type is None or not has_required_template_facts(template_type, plan.facts):
            return self._build_fallback_response(plan)

        text = render_deterministic_template(template_type, plan.facts)
        return build_generated_response(
            text=bound_response_text(text),
            response_type=plan.response_type,
            channel=plan.channel,
            used_fallback=False,
            mode=ReceptionistResponseMode.DETERMINISTIC,
            safety_level=plan.safety_level,
            facts=plan.facts,
            metadata={
                "generation_source": "deterministic",
                "mode": ReceptionistResponseMode.DETERMINISTIC.value,
                "template_type": template_type.value,
            },
        )

    def _build_fallback_response(self, plan: ResponsePlan) -> GeneratedResponse:
        return build_generated_response(
            text=bound_response_text(plan.fallback_text),
            response_type=plan.response_type,
            channel=plan.channel,
            used_fallback=True,
            mode=ReceptionistResponseMode.DETERMINISTIC,
            safety_level=plan.safety_level,
            facts=plan.facts,
            metadata={
                "generation_source": "deterministic",
                "mode": ReceptionistResponseMode.DETERMINISTIC.value,
                "used_fallback": True,
            },
        )


class LLMReceptionistResponseGenerator:
    def __init__(
        self,
        *,
        provider: LLMProvider,
        provider_name: LLMProviderName,
        deterministic_generator: DeterministicReceptionistResponseGenerator,
        max_tokens: int = 400,
        temperature: float = 0.0,
        validate_output: bool = True,
        output_validator: ResponseOutputValidator | None = None,
    ) -> None:
        if max_tokens < 1:
            msg = "max_tokens must be >= 1"
            raise ValueError(msg)

        self.provider = provider
        self.provider_name = provider_name
        self.deterministic_generator = deterministic_generator
        self.max_tokens = max_tokens
        self.temperature = temperature
        self.validate_output = validate_output
        self.output_validator = output_validator or ResponseOutputValidator()

    def generate(self, plan: ResponsePlan) -> GeneratedResponse:
        if should_use_deterministic_response(plan):
            return self.deterministic_generator.generate(plan)

        prompt_version = get_current_receptionist_response_prompt_metadata().version
        llm_request = self._build_llm_request(plan=plan, prompt_version=prompt_version)

        try:
            provider_response = self.provider.complete(llm_request)
            parse_outcome = parse_structured_output_with_repair_flag(
                raw_output=provider_response.content,
                model_type=ReceptionistLLMPhrasedResponse,
            )
            phrased = parse_outcome.value
            text = phrased.text
            if self.validate_output:
                text = self.output_validator.validate(text=text, plan=plan)

            return build_generated_response(
                text=bound_response_text(text),
                response_type=plan.response_type,
                channel=plan.channel,
                used_fallback=False,
                mode=ReceptionistResponseMode.LLM,
                safety_level=plan.safety_level,
                facts=plan.facts,
                metadata={
                    "generation_source": "llm",
                    "mode": ReceptionistResponseMode.LLM.value,
                    "prompt_version": prompt_version,
                    "provider": self.provider_name.value,
                    "model": provider_response.model,
                    "input_tokens": provider_response.input_tokens,
                    "output_tokens": provider_response.output_tokens,
                    "estimated_cost_micros": provider_response.estimated_cost_micros,
                },
            )
        except LLMProviderError as exc:
            return self._deterministic_fallback(
                plan,
                failure_reason=type(exc).__name__,
            )
        except (StructuredOutputParseError, StructuredOutputValidationError) as exc:
            return self._deterministic_fallback(
                plan,
                failure_reason=type(exc).__name__,
            )
        except ResponseOutputValidationError as exc:
            return self._deterministic_fallback(
                plan,
                failure_reason=type(exc).__name__,
            )
        except Exception:
            logger.exception(
                "receptionist_response_generation_unknown_error",
                extra={"event": "receptionist_response_generation_unknown_error"},
            )
            return self._deterministic_fallback(
                plan,
                failure_reason="unknown_error",
            )

    def _build_llm_request(self, *, plan: ResponsePlan, prompt_version: str) -> LLMRequest:
        return LLMRequest(
            messages=[
                LLMMessage(
                    role="system",
                    content=build_receptionist_response_system_prompt(),
                ),
                LLMMessage(
                    role="user",
                    content=json.dumps(build_response_plan_payload(plan)),
                ),
            ],
            response_format="json",
            temperature=self.temperature,
            max_tokens=self.max_tokens,
            metadata={
                "component": "receptionist_response_generator",
                "prompt_version": prompt_version,
            },
        )

    def _deterministic_fallback(
        self,
        plan: ResponsePlan,
        *,
        failure_reason: str,
    ) -> GeneratedResponse:
        deterministic = self.deterministic_generator.generate(plan)
        metadata = {
            "generation_source": "deterministic",
            "mode": ReceptionistResponseMode.DETERMINISTIC.value,
            "used_fallback": True,
            "failure_reason": failure_reason,
        }
        if deterministic.metadata.get("template_type") is not None:
            metadata["template_type"] = str(deterministic.metadata["template_type"])

        return build_generated_response(
            text=deterministic.text,
            response_type=plan.response_type,
            channel=plan.channel,
            used_fallback=True,
            mode=ReceptionistResponseMode.DETERMINISTIC,
            safety_level=plan.safety_level,
            facts=plan.facts,
            metadata=metadata,
        )


def build_receptionist_response_generator_from_settings(
    settings: Settings,
) -> ReceptionistResponseGenerator:
    deterministic_generator = DeterministicReceptionistResponseGenerator()
    if settings.receptionist_response_mode == ReceptionistResponseMode.DETERMINISTIC:
        return deterministic_generator

    provider_name = settings.resolved_receptionist_response_llm_provider
    provider = create_llm_provider_from_settings(settings, provider_name)
    return LLMReceptionistResponseGenerator(
        provider=provider,
        provider_name=provider_name,
        deterministic_generator=deterministic_generator,
        max_tokens=settings.receptionist_response_max_tokens,
        temperature=settings.receptionist_response_temperature,
        validate_output=settings.receptionist_response_validate_output,
    )


def should_use_deterministic_response(plan: ResponsePlan) -> bool:
    if plan.deterministic_behavior:
        return True

    if plan.response_type == ReceptionistResponseType.CRITICAL:
        return True

    if plan.safety_level == ReceptionistResponseSafetyLevel.CRITICAL:
        return True

    template_type = resolve_template_type(plan.facts)
    return template_type == ReceptionistTemplateType.EMERGENCY_GUIDANCE


def build_response_plan_payload(plan: ResponsePlan) -> dict[str, Any]:
    return {
        "response_type": plan.response_type.value,
        "channel": plan.channel.value,
        "fallback_text": plan.fallback_text,
        "safety_level": plan.safety_level.value if plan.safety_level is not None else None,
        "facts": plan.facts,
        "metadata": plan.metadata,
        "deterministic_behavior": plan.deterministic_behavior,
    }


def resolve_template_type(facts: dict[str, Any]) -> ReceptionistTemplateType | None:
    raw_template_type = facts.get("template_type")
    if raw_template_type is None:
        return None

    normalized = str(raw_template_type).strip().lower()
    if not normalized:
        return None

    try:
        return ReceptionistTemplateType(normalized)
    except ValueError:
        return None


def has_required_template_facts(
    template_type: ReceptionistTemplateType,
    facts: dict[str, Any],
) -> bool:
    required_keys = _REQUIRED_TEMPLATE_FACTS.get(template_type, frozenset())
    for key in required_keys:
        value = facts.get(key)
        if value is None:
            return False
        if isinstance(value, str) and not value.strip():
            return False
    return True


def bound_response_text(text: str) -> str:
    normalized = text.strip()
    if len(normalized) <= MAX_DETERMINISTIC_RESPONSE_TEXT_LENGTH:
        return normalized

    return normalized[: MAX_DETERMINISTIC_RESPONSE_TEXT_LENGTH - 3].rstrip() + "..."


def render_deterministic_template(
    template_type: ReceptionistTemplateType,
    facts: dict[str, Any],
) -> str:
    specialty_name = _safe_fact_text(facts.get("specialty_name"))
    requested_date = _safe_fact_text(facts.get("requested_date"))
    time_window_label = _safe_fact_text(facts.get("time_window_label"))
    offered_slot_count = facts.get("offered_slot_count")
    offered_slots_summary = _safe_fact_text(facts.get("offered_slots_summary"))
    hold_id = _safe_fact_text(facts.get("hold_id"))
    appointment_id = _safe_fact_text(facts.get("appointment_id"))
    doctor_name = _safe_fact_text(facts.get("doctor_name"))
    failure_reason = _safe_fact_text(facts.get("failure_reason"))

    if template_type == ReceptionistTemplateType.GREETING:
        return (
            "Hello, I am the clinic receptionist assistant. I can help with "
            "appointments, cancellations, and rescheduling."
        )

    if template_type == ReceptionistTemplateType.ASK_FOR_SPECIALTY:
        return "Please tell me the specialty or doctor you would like to see."

    if template_type == ReceptionistTemplateType.ASK_FOR_DATE:
        if specialty_name is not None:
            return f"Please provide the appointment date you prefer for {specialty_name}."
        return "Please provide the appointment date you prefer."

    if template_type == ReceptionistTemplateType.ASK_FOR_TIME_PREFERENCE:
        return (
            f"Please specify a time-of-day preference for {requested_date}, such as "
            "morning, afternoon, or evening."
        )

    if template_type == ReceptionistTemplateType.AVAILABILITY_OPTIONS:
        count = int(str(offered_slot_count))
        if offered_slots_summary is not None:
            return (
                f"I found {count} available appointments on {requested_date}. "
                f"{offered_slots_summary}"
            )
        if time_window_label is not None:
            return (
                f"I found {count} available appointments on {requested_date} "
                f"during the {time_window_label}."
            )
        return f"I found {count} available appointments on {requested_date}."

    if template_type == ReceptionistTemplateType.SLOT_HOLD_CREATED:
        if doctor_name is not None:
            return (
                f"I temporarily held a slot with {doctor_name}. Your hold reference is "
                f"{hold_id}. This is not booked yet. Please provide patient details to "
                "confirm."
            )
        return (
            f"I temporarily held a slot for you. Your hold reference is {hold_id}. "
            "This is not booked yet. Please provide patient details to confirm."
        )

    if template_type == ReceptionistTemplateType.ASK_FOR_PATIENT_IDENTITY:
        return (
            "To continue, please provide the patient's full name, date of birth, phone, and email."
        )

    if template_type == ReceptionistTemplateType.ASK_FOR_CONFIRMATION:
        return "I have your patient details on file. Please confirm to book the held appointment."

    if template_type == ReceptionistTemplateType.BOOKING_SUCCEEDED:
        return f"Your appointment is confirmed. Reference: {appointment_id}."

    if template_type == ReceptionistTemplateType.BOOKING_FAILED:
        if failure_reason is not None:
            return f"I could not complete the booking. {failure_reason}"
        return "I could not complete the booking. Please try again or choose another time."

    if template_type == ReceptionistTemplateType.CANCELLATION_SUCCEEDED:
        return f"The appointment {appointment_id} has been cancelled."

    if template_type == ReceptionistTemplateType.CANCELLATION_FAILED:
        if failure_reason is not None:
            return f"I could not cancel the appointment. {failure_reason}"
        return "I could not cancel the appointment. Please verify the appointment details."

    if template_type == ReceptionistTemplateType.RESCHEDULE_SUCCEEDED:
        return f"Your appointment has been rescheduled. New reference: {appointment_id}."

    if template_type == ReceptionistTemplateType.RESCHEDULE_FAILED:
        if failure_reason is not None:
            return f"I could not reschedule the appointment. {failure_reason}"
        return "I could not reschedule the appointment. Please try another time."

    if template_type == ReceptionistTemplateType.EMERGENCY_GUIDANCE:
        return (
            "If this is a medical emergency, please call emergency services or go to "
            "the nearest emergency room."
        )

    if template_type == ReceptionistTemplateType.HUMAN_ESCALATION:
        return (
            "I'll mark this conversation for human follow-up. A human receptionist can review it."
        )

    if template_type == ReceptionistTemplateType.UNSUPPORTED_REQUEST:
        return (
            "I can help with clinic scheduling questions. Please tell me whether you "
            "want to book, cancel, or reschedule an appointment."
        )

    if template_type == ReceptionistTemplateType.GENERIC_ERROR:
        return "Something went wrong while handling your request. Please try again in a moment."

    msg = f"unsupported template type: {template_type}"
    raise ValueError(msg)


def _safe_fact_text(value: Any) -> str | None:
    if value is None:
        return None

    if isinstance(value, str):
        normalized = value.strip()
        return normalized or None

    return str(value)
