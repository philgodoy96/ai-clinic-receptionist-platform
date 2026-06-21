from __future__ import annotations

from typing import cast

import pytest

from app.ai.fake_llm_provider import FakeLLMProvider
from app.services.appointment_booking import AppointmentBookingService
from app.services.chat_receptionist import (
    ChatMessageInput,
    ChatReceptionistIntent,
    ChatReceptionistService,
)
from app.services.conversations import ConversationService
from app.services.llm_receptionist import LLMReceptionistAnalysisService
from app.services.slot_filling import LLMChatSlotFillingService
from tests.test_chat_booking_confirmation_flow import (
    FULL_IDENTITY_WITH_CONFIRM,
    create_jane_doe_patient,
)
from tests.test_chat_receptionist_service import (
    TrackingAppointmentBookingService,
    _create_hold_service,
    create_appointment_booking_service_for_scheduling,
    create_chat_receptionist_service,
)
from tests.test_conversations import FakeConversationRepository
from tests.test_scheduling_services import (
    EMILY_JULY_SLOT_1_ID,
    FakeAppointmentRepository,
    create_demo_scheduling_service,
    create_demo_scheduling_service_with_emily_july_availability,
)

SHADOW_METADATA_FIELDS = (
    "confidence",
    "failure_reason",
    "latency_ms",
    "input_tokens",
    "output_tokens",
    "estimated_cost_micros",
    "attempt_count",
)

FORBIDDEN_SHADOW_METADATA_KEYS = (
    "raw_prompt",
    "raw_provider_output",
    "prompt",
    "provider_output",
    "extracted",
    "patient_identity",
)


@pytest.fixture()
def shadow_chat_service() -> tuple[ChatReceptionistService, FakeConversationRepository]:
    repository = FakeConversationRepository()
    conversations = ConversationService(repository=repository)
    scheduling = create_demo_scheduling_service()
    llm_analysis = LLMReceptionistAnalysisService(provider=FakeLLMProvider())
    slot_filling = LLMChatSlotFillingService(scheduling=scheduling)
    service = create_chat_receptionist_service(
        conversations=conversations,
        scheduling=scheduling,
        llm_analysis=llm_analysis,
        slot_filling=slot_filling,
    )

    return service, repository


def test_chat_with_llm_shadow_metadata_keeps_deterministic_intent(
    shadow_chat_service: tuple[ChatReceptionistService, FakeConversationRepository],
) -> None:
    service, _repository = shadow_chat_service

    result = service.handle_message(
        ChatMessageInput(message="I need an appointment"),
    )

    assert result.intent == ChatReceptionistIntent.APPOINTMENT_REQUEST
    assert "llm_shadow_analysis" in result.assistant_message.message_metadata


def test_llm_shadow_metadata_includes_required_fields(
    shadow_chat_service: tuple[ChatReceptionistService, FakeConversationRepository],
) -> None:
    service, _repository = shadow_chat_service

    result = service.handle_message(
        ChatMessageInput(message="I need an appointment"),
    )
    shadow = result.assistant_message.message_metadata["llm_shadow_analysis"]

    for field in SHADOW_METADATA_FIELDS:
        assert field in shadow


def test_llm_shadow_metadata_excludes_sensitive_fields(
    shadow_chat_service: tuple[ChatReceptionistService, FakeConversationRepository],
) -> None:
    service, _repository = shadow_chat_service

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

    for forbidden_key in FORBIDDEN_SHADOW_METADATA_KEYS:
        assert forbidden_key not in shadow
        assert forbidden_key not in metadata


def test_booking_with_llm_shadow_enabled_uses_deterministic_flow() -> None:
    repository = FakeConversationRepository()
    conversations = ConversationService(repository=repository)
    hold_service = _create_hold_service()
    scheduling = create_demo_scheduling_service_with_emily_july_availability(
        patients=[create_jane_doe_patient()],
    )
    inner_booking = create_appointment_booking_service_for_scheduling(
        scheduling,
        hold_service,
    )
    tracking_booking = TrackingAppointmentBookingService(inner_booking)
    llm_analysis = LLMReceptionistAnalysisService(provider=FakeLLMProvider())
    service = create_chat_receptionist_service(
        conversations=conversations,
        scheduling=scheduling,
        hold_service=hold_service,
        appointment_booking=cast(AppointmentBookingService, tracking_booking),
        llm_analysis=llm_analysis,
        slot_filling=None,
    )
    appointments = scheduling.appointments
    assert isinstance(appointments, FakeAppointmentRepository)

    availability = service.handle_message(
        ChatMessageInput(message="Dr. Emily Carter on 2026-07-02"),
    )
    hold = service.handle_message(
        ChatMessageInput(
            message="I'll take 09:00",
            conversation_id=availability.conversation.id,
        ),
    )

    result = service.handle_message(
        ChatMessageInput(
            message=FULL_IDENTITY_WITH_CONFIRM,
            conversation_id=hold.conversation.id,
        ),
    )

    assert result.intent == ChatReceptionistIntent.BOOKING_CONFIRMED
    assert result.booking_confirmed is True
    assert len(tracking_booking.book_calls) == 1
    assert appointments.appointments[0].availability_slot_id == EMILY_JULY_SLOT_1_ID
    assert "llm_shadow_analysis" in result.assistant_message.message_metadata
