from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from app.domain.conversations.enums import ConversationChannel
from app.domain.receptionist.enums import (
    ReceptionistResponseMode,
    ReceptionistResponseSafetyLevel,
    ReceptionistResponseType,
    ReceptionistTemplateType,
)
from app.domain.receptionist.response_planning import (
    GeneratedResponse,
    ResponsePlan,
    build_response_plan,
)
from app.models.conversations import Conversation
from app.services.receptionist_response_generator import (
    DeterministicReceptionistResponseGenerator,
    ReceptionistResponseGenerator,
)

_CONTROLLED_CHAT_INTENTS = frozenset(
    {
        "emergency",
        "human_escalation_requested",
        "booking_confirmed",
        "booking_conflict",
        "booking_hold_missing",
        "booking_hold_expired",
        "hold_created",
    },
)

_CHAT_INTENT_TEMPLATE_MAP: dict[str, ReceptionistTemplateType] = {
    "greeting": ReceptionistTemplateType.GREETING,
    "appointment_request": ReceptionistTemplateType.ASK_FOR_SPECIALTY,
    "availability_missing_date": ReceptionistTemplateType.ASK_FOR_DATE,
    "invalid_date": ReceptionistTemplateType.ASK_FOR_DATE,
    "invalid_time_preference": ReceptionistTemplateType.ASK_FOR_TIME_PREFERENCE,
    "availability_results": ReceptionistTemplateType.AVAILABILITY_OPTIONS,
    "hold_created": ReceptionistTemplateType.SLOT_HOLD_CREATED,
    "booking_identity_missing": ReceptionistTemplateType.ASK_FOR_PATIENT_IDENTITY,
    "patient_identity_partial": ReceptionistTemplateType.ASK_FOR_PATIENT_IDENTITY,
    "booking_confirmation_required": ReceptionistTemplateType.ASK_FOR_CONFIRMATION,
    "booking_confirmed": ReceptionistTemplateType.BOOKING_SUCCEEDED,
    "booking_conflict": ReceptionistTemplateType.BOOKING_FAILED,
    "booking_hold_missing": ReceptionistTemplateType.BOOKING_FAILED,
    "booking_hold_expired": ReceptionistTemplateType.BOOKING_FAILED,
    "emergency": ReceptionistTemplateType.EMERGENCY_GUIDANCE,
    "human_escalation_requested": ReceptionistTemplateType.HUMAN_ESCALATION,
    "fallback": ReceptionistTemplateType.UNSUPPORTED_REQUEST,
    "escalation_suggested": ReceptionistTemplateType.UNSUPPORTED_REQUEST,
}

_INFORMATIONAL_CHAT_INTENTS = frozenset(
    {
        "list_specialties",
        "list_doctors",
        "specialty_doctors",
        "greeting",
    },
)


@dataclass(frozen=True, slots=True)
class ChatReplySnapshot:
    intent: str
    content: str
    matched_specialty_name: str | None = None
    offered_slot_count: int | None = None
    hold_id: str | None = None
    appointment_id: str | None = None


def build_response_plan_from_chat_reply(
    reply: ChatReplySnapshot,
    *,
    conversation: Conversation,
    response_mode: ReceptionistResponseMode,
) -> ResponsePlan:
    controlled = reply.intent in _CONTROLLED_CHAT_INTENTS
    facts = _build_chat_facts(reply, conversation)
    template_type = _CHAT_INTENT_TEMPLATE_MAP.get(reply.intent)
    use_exact_fallback = (
        response_mode == ReceptionistResponseMode.DETERMINISTIC
        or controlled
        or reply.intent == "escalation_suggested"
    )

    if template_type is not None and not use_exact_fallback:
        facts["template_type"] = template_type.value

    safety_level = None
    if reply.intent == "emergency":
        safety_level = ReceptionistResponseSafetyLevel.CRITICAL

    return build_response_plan(
        response_type=_response_type_for_intent(reply.intent),
        channel=ConversationChannel.CHAT,
        fallback_text=reply.content,
        safety_level=safety_level,
        facts=facts,
        deterministic_behavior=controlled,
    )


def render_chat_reply(
    reply: ChatReplySnapshot,
    *,
    conversation: Conversation,
    response_generator: ReceptionistResponseGenerator,
    response_mode: ReceptionistResponseMode,
) -> GeneratedResponse:
    plan = build_response_plan_from_chat_reply(
        reply,
        conversation=conversation,
        response_mode=response_mode,
    )
    return response_generator.generate(plan)


def build_suggested_retell_response_text(
    *,
    template_type: ReceptionistTemplateType,
    facts: dict[str, Any],
    fallback_text: str,
    response_type: ReceptionistResponseType,
    deterministic_generator: DeterministicReceptionistResponseGenerator | None = None,
) -> str:
    generator = deterministic_generator or DeterministicReceptionistResponseGenerator()
    plan = build_response_plan(
        response_type=response_type,
        channel=ConversationChannel.RETELL_VOICE,
        fallback_text=fallback_text,
        facts={
            **facts,
            "template_type": template_type.value,
        },
        deterministic_behavior=True,
    )
    return generator.generate(plan).text


def chat_reply_snapshot_from_reply(reply: Any) -> ChatReplySnapshot:
    return ChatReplySnapshot(
        intent=reply.intent.value,
        content=reply.content,
        matched_specialty_name=reply.matched_specialty_name,
        offered_slot_count=reply.offered_slot_count,
        hold_id=reply.hold_id,
        appointment_id=reply.appointment_id,
    )


def _build_chat_facts(
    reply: ChatReplySnapshot,
    conversation: Conversation,
) -> dict[str, Any]:
    facts: dict[str, Any] = {"intent": reply.intent}
    chat_context = conversation.conversation_metadata.get("chat_context", {})
    if not isinstance(chat_context, dict):
        chat_context = {}

    if reply.matched_specialty_name is not None:
        facts["specialty_name"] = reply.matched_specialty_name

    selected_doctor_name = chat_context.get("selected_doctor_name")
    if isinstance(selected_doctor_name, str) and selected_doctor_name.strip():
        facts["doctor_name"] = selected_doctor_name.strip()

    requested_date = chat_context.get("requested_date")
    if isinstance(requested_date, str) and requested_date.strip():
        facts["requested_date"] = requested_date.strip()

    requested_time_window = chat_context.get("requested_time_window")
    if isinstance(requested_time_window, dict):
        label = requested_time_window.get("label")
        if isinstance(label, str) and label.strip():
            facts["time_window_label"] = label.strip()

    if reply.offered_slot_count is not None:
        facts["offered_slot_count"] = reply.offered_slot_count

    if reply.hold_id is not None:
        facts["hold_id"] = reply.hold_id

    if reply.appointment_id is not None:
        facts["appointment_id"] = reply.appointment_id

    return facts


def _response_type_for_intent(intent: str) -> ReceptionistResponseType:
    if intent == "emergency":
        return ReceptionistResponseType.CRITICAL
    if intent in {"human_escalation_requested", "escalation_suggested"}:
        return ReceptionistResponseType.ESCALATION
    if intent in {"fallback", "invalid_date", "invalid_time_preference"}:
        return ReceptionistResponseType.FALLBACK
    if intent in _INFORMATIONAL_CHAT_INTENTS:
        return ReceptionistResponseType.INFORMATIONAL
    if intent in {
        "booking_confirmation_required",
        "booking_confirmed",
        "booking_conflict",
        "booking_hold_missing",
        "booking_hold_expired",
        "booking_identity_missing",
        "patient_identity_partial",
        "patient_identity_complete",
    }:
        return ReceptionistResponseType.CONFIRMATION
    return ReceptionistResponseType.SCHEDULING
