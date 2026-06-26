from __future__ import annotations

import json
from typing import cast

from app.ai.llm_provider import LLMProviderName
from app.domain.receptionist.enums import ReceptionistResponseMode
from app.services.appointment_booking import AppointmentBookingService
from app.services.chat_receptionist import ChatMessageInput, ChatReceptionistIntent
from app.services.conversations import ConversationService
from app.services.receptionist_response_generator import (
    DeterministicReceptionistResponseGenerator,
    LLMReceptionistResponseGenerator,
)
from tests.chat_booking_flow_support import complete_new_patient_booking
from tests.llm_provider_test_helpers import RaisingLLMProvider, StaticContentLLMProvider
from tests.llm_reliability_test_helpers import CountingLLMProvider
from tests.test_chat_receptionist_service import (
    TrackingAppointmentBookingService,
    _create_hold_service,
    create_appointment_booking_service_for_scheduling,
    create_chat_receptionist_service,
)
from tests.test_conversations import FakeConversationRepository
from tests.test_scheduling_services import (
    create_demo_scheduling_service,
    create_demo_scheduling_service_with_emily_july_availability,
)


def test_deterministic_mode_preserves_expected_greeting_response() -> None:
    repository = FakeConversationRepository()
    conversations = ConversationService(repository=repository)
    scheduling = create_demo_scheduling_service()
    service = create_chat_receptionist_service(
        conversations=conversations,
        scheduling=scheduling,
        response_generator=DeterministicReceptionistResponseGenerator(),
        response_generation_mode=ReceptionistResponseMode.DETERMINISTIC,
    )

    result = service.handle_message(ChatMessageInput(message="Hello"))

    assert result.intent == ChatReceptionistIntent.GREETING
    assert result.reply == (
        "Hello, I am the clinic receptionist assistant. I can help with "
        "appointments, cancellations, and rescheduling."
    )
    assert result.assistant_message.message_metadata["response_generation"]["mode"] == (
        "deterministic"
    )


def test_mocked_llm_mode_returns_natural_response_for_safe_response_type() -> None:
    repository = FakeConversationRepository()
    conversations = ConversationService(repository=repository)
    scheduling = create_demo_scheduling_service()
    llm_generator = LLMReceptionistResponseGenerator(
        provider=StaticContentLLMProvider(
            json.dumps({"text": "Which specialty would you like to book today?"}),
        ),
        provider_name=LLMProviderName.FAKE,
        deterministic_generator=DeterministicReceptionistResponseGenerator(),
    )
    service = create_chat_receptionist_service(
        conversations=conversations,
        scheduling=scheduling,
        response_generator=llm_generator,
        response_generation_mode=ReceptionistResponseMode.LLM,
    )

    result = service.handle_message(ChatMessageInput(message="I need an appointment"))

    assert result.intent == ChatReceptionistIntent.APPOINTMENT_REQUEST
    assert result.reply == "Which specialty would you like to book today?"
    assert result.assistant_message.message_metadata["response_generation"]["mode"] == "llm"


def test_llm_failure_falls_back_to_deterministic_content() -> None:
    repository = FakeConversationRepository()
    conversations = ConversationService(repository=repository)
    scheduling = create_demo_scheduling_service()
    llm_generator = LLMReceptionistResponseGenerator(
        provider=RaisingLLMProvider(),
        provider_name=LLMProviderName.FAKE,
        deterministic_generator=DeterministicReceptionistResponseGenerator(),
    )
    service = create_chat_receptionist_service(
        conversations=conversations,
        scheduling=scheduling,
        response_generator=llm_generator,
        response_generation_mode=ReceptionistResponseMode.LLM,
    )

    result = service.handle_message(ChatMessageInput(message="I need an appointment"))

    assert result.intent == ChatReceptionistIntent.APPOINTMENT_REQUEST
    assert "specialty" in result.reply.lower()
    assert result.assistant_message.message_metadata["response_generation"]["used_fallback"] is True
    assert result.assistant_message.message_metadata["response_generation"]["mode"] == (
        "deterministic"
    )


def test_emergency_response_does_not_call_llm() -> None:
    repository = FakeConversationRepository()
    conversations = ConversationService(repository=repository)
    scheduling = create_demo_scheduling_service()
    provider = CountingLLMProvider(json.dumps({"text": "ignored"}))
    llm_generator = LLMReceptionistResponseGenerator(
        provider=provider,
        provider_name=LLMProviderName.FAKE,
        deterministic_generator=DeterministicReceptionistResponseGenerator(),
    )
    service = create_chat_receptionist_service(
        conversations=conversations,
        scheduling=scheduling,
        response_generator=llm_generator,
        response_generation_mode=ReceptionistResponseMode.LLM,
    )

    result = service.handle_message(ChatMessageInput(message="This is a medical emergency"))

    assert provider.call_count == 0
    assert result.intent == ChatReceptionistIntent.EMERGENCY
    assert "emergency" in result.reply.lower()
    assert result.assistant_message.message_metadata["response_generation"]["mode"] == (
        "deterministic"
    )


def test_booking_confirmation_does_not_depend_on_llm_output() -> None:
    repository = FakeConversationRepository()
    conversations = ConversationService(repository=repository)
    holds = _create_hold_service()
    scheduling = create_demo_scheduling_service_with_emily_july_availability(
        patients=[],
    )
    booking = TrackingAppointmentBookingService(
        create_appointment_booking_service_for_scheduling(scheduling, holds),
    )
    provider = CountingLLMProvider(json.dumps({"text": "Booked by LLM only"}))
    llm_generator = LLMReceptionistResponseGenerator(
        provider=provider,
        provider_name=LLMProviderName.FAKE,
        deterministic_generator=DeterministicReceptionistResponseGenerator(),
    )
    service = create_chat_receptionist_service(
        conversations=conversations,
        scheduling=scheduling,
        hold_service=holds,
        appointment_booking=cast(AppointmentBookingService, booking),
        response_generator=llm_generator,
        response_generation_mode=ReceptionistResponseMode.LLM,
    )
    availability = service.handle_message(
        ChatMessageInput(message="Dr. Emily Carter on 2026-07-02"),
    )
    hold = service.handle_message(
        ChatMessageInput(
            message="I'll take 09:00",
            conversation_id=availability.conversation.id,
        ),
    )

    result = complete_new_patient_booking(service, hold.conversation)

    assert result.booking_confirmed is True
    assert booking.book_calls
    assert result.intent == ChatReceptionistIntent.BOOKING_CONFIRMED
    assert result.assistant_message.message_metadata["response_generation"]["mode"] == (
        "deterministic"
    )
    assert "Booked by LLM only" not in result.reply


def test_retell_tool_result_includes_provider_safe_suggested_response_text() -> None:
    from types import SimpleNamespace

    from app.services.retell_tool_adapter import RetellToolCallingAdapter

    adapter = RetellToolCallingAdapter(
        scheduling_service=SimpleNamespace(),
        hold_service=SimpleNamespace(),
        voice_calls=SimpleNamespace(),
    )
    booking_result = SimpleNamespace(
        appointment_id="apt-safe-123",
        patient_id="patient-1",
        availability_slot_id="slot-1",
        hold_id="hold-1",
        confirmation_email_created=False,
    )

    result = adapter._build_book_appointment_result(booking_result)

    assert "suggested_response_text" in result
    suggested = result["suggested_response_text"].lower()
    assert "you're all set" in suggested
    assert "apt-safe-123" not in suggested
    assert "slot" not in suggested
    assert "reference" not in suggested
    assert "api_key" not in suggested
    assert "raw_provider_output" not in result
