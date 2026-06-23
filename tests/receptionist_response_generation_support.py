from __future__ import annotations

import json
import re
from typing import cast

from app.ai.llm_provider import LLMProviderName
from app.domain.receptionist.enums import ReceptionistResponseMode, ReceptionistTemplateType
from app.services.appointment_booking import AppointmentBookingService
from app.services.chat_receptionist import ChatReceptionistService
from app.services.conversation_health import ConversationHealthService
from app.services.conversations import ConversationService
from app.services.email_jobs import EmailJobService
from app.services.human_escalations import HumanEscalationService
from app.services.human_handoff_notifications import HumanHandoffNotificationService
from app.services.receptionist_response_generator import (
    DeterministicReceptionistResponseGenerator,
    LLMReceptionistResponseGenerator,
)
from app.services.receptionist_response_planning import (
    ChatReplySnapshot,
    build_response_plan_from_chat_reply,
)
from tests.llm_reliability_test_helpers import CountingLLMProvider
from tests.test_chat_receptionist_service import (
    TrackingAppointmentBookingService,
    _create_hold_service,
    create_appointment_booking_service_for_scheduling,
    create_chat_receptionist_service,
)
from tests.test_conversations import FakeConversationRepository
from tests.test_email_jobs import FakeEmailJobRepository
from tests.test_human_escalations import FakeHumanEscalationRepository
from tests.test_scheduling_services import (
    create_demo_scheduling_service,
    create_demo_scheduling_service_with_emily_july_availability,
)

FORBIDDEN_METADATA_MARKERS = frozenset(
    {
        "api_key",
        "groq_api_key",
        "raw_payload",
        "raw_provider_output",
        "raw_prompt",
        "system_prompt",
        "provider_output",
        "choices",
        "completion",
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

_TIME_PATTERN = re.compile(r"\b(\d{2}:\d{2})\b")


def build_llm_generator_with_provider(provider: object) -> LLMReceptionistResponseGenerator:
    return LLMReceptionistResponseGenerator(
        provider=provider,  # type: ignore[arg-type]
        provider_name=LLMProviderName.FAKE,
        deterministic_generator=DeterministicReceptionistResponseGenerator(),
    )


def build_counting_llm_chat_service(
    *,
    content: str,
    response_generation_mode: ReceptionistResponseMode = ReceptionistResponseMode.LLM,
) -> tuple[ChatReceptionistService, CountingLLMProvider]:
    repository = FakeConversationRepository()
    conversations = ConversationService(repository=repository)
    scheduling = create_demo_scheduling_service()
    provider = CountingLLMProvider(content)
    service = create_chat_receptionist_service(
        conversations=conversations,
        scheduling=scheduling,
        response_generator=build_llm_generator_with_provider(provider),
        response_generation_mode=response_generation_mode,
    )
    return service, provider


def build_human_escalation_chat_service(
    *,
    response_generation_mode: ReceptionistResponseMode = ReceptionistResponseMode.LLM,
) -> tuple[ChatReceptionistService, CountingLLMProvider]:
    repository = FakeConversationRepository()
    conversations = ConversationService(repository=repository)
    scheduling = create_demo_scheduling_service()
    email_jobs = EmailJobService(repository=FakeEmailJobRepository())
    provider = CountingLLMProvider(json.dumps({"text": "I will connect you to a human now."}))
    service = create_chat_receptionist_service(
        conversations=conversations,
        scheduling=scheduling,
        conversation_health=ConversationHealthService(),
        human_escalations=HumanEscalationService(
            repository=FakeHumanEscalationRepository(),
        ),
        human_handoff_notifications=HumanHandoffNotificationService(email_jobs=email_jobs),
        response_generator=build_llm_generator_with_provider(provider),
        response_generation_mode=response_generation_mode,
    )
    return service, provider


def build_availability_chat_service() -> ChatReceptionistService:
    repository = FakeConversationRepository()
    conversations = ConversationService(repository=repository)
    scheduling = create_demo_scheduling_service_with_emily_july_availability()
    return create_chat_receptionist_service(
        conversations=conversations,
        scheduling=scheduling,
        hold_service=_create_hold_service(),
        appointment_booking=cast(
            AppointmentBookingService,
            TrackingAppointmentBookingService(
                create_appointment_booking_service_for_scheduling(
                    scheduling,
                    _create_hold_service(),
                ),
            ),
        ),
        response_generation_mode=ReceptionistResponseMode.DETERMINISTIC,
    )


def extract_reply_times(reply: str) -> set[str]:
    return set(_TIME_PATTERN.findall(reply))


def assert_metadata_has_no_secrets(metadata: dict[str, object]) -> None:
    for key, value in metadata.items():
        normalized_key = key.strip().lower()
        assert normalized_key not in FORBIDDEN_METADATA_MARKERS
        normalized_value = str(value).lower()
        for marker in FORBIDDEN_METADATA_MARKERS:
            assert marker not in normalized_value


def assert_controlled_plan_uses_deterministic_behavior(
    reply: ChatReplySnapshot,
    *,
    conversation: object,
    response_mode: ReceptionistResponseMode,
) -> None:
    plan = build_response_plan_from_chat_reply(
        reply,
        conversation=conversation,  # type: ignore[arg-type]
        response_mode=response_mode,
    )
    assert plan.deterministic_behavior is True
