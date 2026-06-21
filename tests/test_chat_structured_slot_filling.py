from __future__ import annotations

import json
from typing import cast

from app.ai.fake_llm_provider import FakeLLMProvider
from app.ai.llm_provider import LLMProvider
from app.ai.reliability import LLMFailureReason
from app.services.appointment_booking import AppointmentBookingService
from app.services.chat_receptionist import (
    ChatMessageInput,
    ChatReceptionistIntent,
    ChatReceptionistService,
)
from app.services.conversations import ConversationService
from app.services.date_parsing import NaturalLanguageDateParser
from app.services.llm_receptionist import LLMReceptionistAnalysisService
from app.services.scheduling import SchedulingService
from app.services.slot_filling import LLMChatSlotFillingService
from app.services.time_preferences import TimePreferenceParser
from tests.test_chat_receptionist_service import (
    TrackingAppointmentBookingService,
    _create_hold_service,
    create_appointment_booking_service_for_scheduling,
    create_chat_receptionist_service,
)
from tests.test_conversations import FakeConversationRepository
from tests.test_llm_receptionist_analysis import (
    RaisingLLMProvider,
    StaticContentLLMProvider,
)
from tests.test_scheduling_services import create_demo_scheduling_service


def create_structured_slot_filling_chat_service(
    *,
    llm_provider: LLMProvider | RaisingLLMProvider | StaticContentLLMProvider,
    scheduling: SchedulingService | None = None,
    appointment_booking: AppointmentBookingService | None = None,
) -> ChatReceptionistService:
    repository = FakeConversationRepository()
    conversations = ConversationService(repository=repository)
    scheduling_service = scheduling or create_demo_scheduling_service()
    slot_filling = LLMChatSlotFillingService(
        scheduling=scheduling_service,
        date_parser=NaturalLanguageDateParser(),
        time_preference_parser=TimePreferenceParser(),
    )
    llm_analysis = LLMReceptionistAnalysisService(provider=llm_provider)

    return create_chat_receptionist_service(
        conversations=conversations,
        scheduling=scheduling_service,
        llm_analysis=llm_analysis,
        slot_filling=slot_filling,
        appointment_booking=appointment_booking,
    )


def test_eligible_llm_analysis_applies_slot_filling() -> None:
    service = create_structured_slot_filling_chat_service(llm_provider=FakeLLMProvider())

    result = service.handle_message(
        ChatMessageInput(message="I need dermatology on 2026-07-02"),
    )

    chat_context = result.conversation.conversation_metadata["chat_context"]
    assert chat_context["selected_specialty_name"] == "Dermatology"
    assert chat_context["requested_date"] == "2026-07-02"

    slot_filling = result.assistant_message.message_metadata["slot_filling"]
    assert slot_filling["used_llm_analysis"] is True
    applied_fields = {item["field"] for item in slot_filling["applied_fields"]}
    assert "specialty" in applied_fields
    assert "date" in applied_fields


def test_low_confidence_analysis_does_not_apply_slot_filling() -> None:
    low_confidence_payload = json.dumps(
        {
            "intent": "appointment_request",
            "confidence": 0.4,
            "urgency": "normal",
            "extracted": {
                "specialty": "Dermatology",
                "doctor_name": None,
                "date": "2026-07-02",
                "time": None,
                "patient_identity": {
                    "full_name": None,
                    "date_of_birth": None,
                    "phone": None,
                    "email": None,
                },
            },
        },
    )
    service = create_structured_slot_filling_chat_service(
        llm_provider=StaticContentLLMProvider(low_confidence_payload),
    )

    result = service.handle_message(
        ChatMessageInput(message="Hello there"),
    )

    chat_context = result.conversation.conversation_metadata.get("chat_context", {})
    assert "selected_specialty_name" not in chat_context
    assert "requested_date" not in chat_context

    slot_filling = result.assistant_message.message_metadata["slot_filling"]
    assert slot_filling["used_llm_analysis"] is False
    assert slot_filling["rejected_fields"] == [
        {
            "field": "analysis",
            "value": None,
            "reason": "analysis_not_eligible",
        },
    ]
    assert (
        result.assistant_message.message_metadata["llm_shadow_analysis"]["failure_reason"]
        == LLMFailureReason.LOW_CONFIDENCE.value
    )


def test_provider_fallback_does_not_apply_slot_filling() -> None:
    service = create_structured_slot_filling_chat_service(
        llm_provider=RaisingLLMProvider(),
    )

    result = service.handle_message(
        ChatMessageInput(message="Hello there"),
    )

    chat_context = result.conversation.conversation_metadata.get("chat_context", {})
    assert "selected_specialty_name" not in chat_context
    assert "requested_date" not in chat_context

    slot_filling = result.assistant_message.message_metadata["slot_filling"]
    assert slot_filling["used_llm_analysis"] is False
    assert slot_filling["rejected_fields"][0]["reason"] == "analysis_not_eligible"
    assert result.assistant_message.message_metadata["llm_shadow_analysis"]["used_fallback"] is True


def test_llm_booking_confirmation_cannot_book_without_hold() -> None:
    scheduling = create_demo_scheduling_service()
    inner_booking = create_appointment_booking_service_for_scheduling(
        scheduling,
        hold_service=_create_hold_service(),
    )
    tracking_booking = TrackingAppointmentBookingService(inner_booking)
    service = create_structured_slot_filling_chat_service(
        llm_provider=FakeLLMProvider(),
        scheduling=scheduling,
        appointment_booking=cast(AppointmentBookingService, tracking_booking),
    )

    result = service.handle_message(ChatMessageInput(message="confirm"))

    assert result.intent == ChatReceptionistIntent.BOOKING_HOLD_MISSING
    assert len(tracking_booking.book_calls) == 0


def test_llm_shadow_analysis_excludes_patient_identity() -> None:
    service = create_structured_slot_filling_chat_service(llm_provider=FakeLLMProvider())

    result = service.handle_message(
        ChatMessageInput(
            message=(
                "My name is John Miller, DOB 1985-04-12, phone +1-555-0201, "
                "email john.miller@example.test. Confirm."
            ),
        ),
    )

    metadata = result.assistant_message.message_metadata
    shadow = metadata["llm_shadow_analysis"]
    assert "extracted" not in shadow
    assert "patient_identity" not in shadow

    chat_context = result.conversation.conversation_metadata.get("chat_context", {})
    patient_identity = chat_context.get("patient_identity", {})
    assert patient_identity.get("full_name") == "John Miller"
    assert patient_identity.get("phone") == "+1-555-0201"


def test_emergency_deterministic_behavior_wins_over_slot_filling() -> None:
    service = create_structured_slot_filling_chat_service(llm_provider=FakeLLMProvider())

    result = service.handle_message(
        ChatMessageInput(message="This is an emergency and I need dermatology"),
    )

    assert result.intent == ChatReceptionistIntent.EMERGENCY

    slot_filling = result.assistant_message.message_metadata["slot_filling"]
    assert slot_filling["used_llm_analysis"] is False
    assert slot_filling["rejected_fields"][0]["reason"] == "analysis_not_eligible"

    chat_context = result.conversation.conversation_metadata.get("chat_context", {})
    assert "hold_id" not in chat_context
    assert "appointment_id" not in chat_context
