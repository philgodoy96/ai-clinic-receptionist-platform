from __future__ import annotations

from typing import Any, Protocol

from app.domain.receptionist.enums import (
    ReceptionistResponseMode,
    ReceptionistTemplateType,
)
from app.domain.receptionist.response_planning import (
    GeneratedResponse,
    ResponsePlan,
    build_generated_response,
)

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
            return (
                f"Please provide the appointment date you prefer for {specialty_name}."
            )
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
            "To continue, please provide the patient's full name, date of birth, "
            "phone, and email."
        )

    if template_type == ReceptionistTemplateType.ASK_FOR_CONFIRMATION:
        return (
            "I have your patient details on file. Please confirm to book the held "
            "appointment."
        )

    if template_type == ReceptionistTemplateType.BOOKING_SUCCEEDED:
        return (
            f"Your appointment is confirmed. Reference: {appointment_id}."
        )

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
            "I'll mark this conversation for human follow-up. A human receptionist "
            "can review it."
        )

    if template_type == ReceptionistTemplateType.UNSUPPORTED_REQUEST:
        return (
            "I can help with clinic scheduling questions. Please tell me whether you "
            "want to book, cancel, or reschedule an appointment."
        )

    if template_type == ReceptionistTemplateType.GENERIC_ERROR:
        return (
            "Something went wrong while handling your request. Please try again in a "
            "moment."
        )

    msg = f"unsupported template type: {template_type}"
    raise ValueError(msg)


def _safe_fact_text(value: Any) -> str | None:
    if value is None:
        return None

    if isinstance(value, str):
        normalized = value.strip()
        return normalized or None

    return str(value)
