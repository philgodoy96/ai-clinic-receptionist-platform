from __future__ import annotations

from typing import cast

import pytest

from app.ai.fake_llm_provider import FakeLLMProvider
from app.ai.llm_provider import LLMProvider, LLMProviderError
from app.ai.receptionist_output import ReceptionistLLMIntent
from app.ai.reliability import LLMFailureReason
from app.services.appointment_booking import AppointmentBookingService
from app.services.chat_receptionist import (
    ChatMessageInput,
    ChatReceptionistIntent,
    ChatReceptionistService,
)
from app.services.conversations import ConversationService
from app.services.date_parsing import NaturalLanguageDateParser
from app.services.llm_receptionist import (
    LLMReceptionistAnalysisService,
    ReceptionistAnalysisRequest,
)
from app.services.slot_filling import LLMChatSlotFillingService
from app.services.time_preferences import TimePreferenceParser
from tests.llm_provider_test_helpers import (
    StaticContentLLMProvider,
    build_bedrock_provider,
    build_receptionist_analysis_payload,
    build_sample_llm_request,
    build_valid_converse_response,
    make_botocore_error,
)
from tests.test_chat_receptionist_service import (
    FakeAppointmentHoldService,
    TrackingAppointmentBookingService,
    create_appointment_booking_service_for_scheduling,
    create_chat_receptionist_service,
)
from tests.test_conversations import FakeConversationRepository
from tests.test_scheduling_services import create_demo_scheduling_service


def create_chat_service_with_llm_provider(
    provider: LLMProvider,
) -> tuple[ChatReceptionistService, FakeAppointmentHoldService, TrackingAppointmentBookingService]:
    repository = FakeConversationRepository()
    conversations = ConversationService(repository=repository)
    scheduling = create_demo_scheduling_service()
    hold_service = FakeAppointmentHoldService()
    inner_booking = create_appointment_booking_service_for_scheduling(
        scheduling,
        hold_service,
    )
    tracking_booking = TrackingAppointmentBookingService(inner_booking)
    slot_filling = LLMChatSlotFillingService(
        scheduling=scheduling,
        date_parser=NaturalLanguageDateParser(),
        time_preference_parser=TimePreferenceParser(),
    )
    llm_analysis = LLMReceptionistAnalysisService(provider=provider)
    service = create_chat_receptionist_service(
        conversations=conversations,
        scheduling=scheduling,
        hold_service=hold_service,
        appointment_booking=cast(AppointmentBookingService, tracking_booking),
        llm_analysis=llm_analysis,
        slot_filling=slot_filling,
    )
    return service, hold_service, tracking_booking


def test_bedrock_client_timeout_raises_provider_error() -> None:
    provider, _client = build_bedrock_provider(
        error=make_botocore_error("Read timeout on endpoint URL"),
    )

    with pytest.raises(LLMProviderError, match="Read timeout"):
        provider.complete(build_sample_llm_request())


def test_bedrock_client_timeout_triggers_analysis_service_fallback() -> None:
    provider, _client = build_bedrock_provider(
        error=make_botocore_error("Read timeout on endpoint URL"),
    )
    service = LLMReceptionistAnalysisService(provider=provider)

    result = service.analyze_message(
        ReceptionistAnalysisRequest(
            user_message="Hello",
            conversation_context={},
        ),
    )

    assert result.used_fallback is True
    assert result.failure_reason == LLMFailureReason.PROVIDER_ERROR
    assert result.error is not None
    assert "Read timeout" in result.error


def test_invalid_json_from_provider_triggers_invalid_json_fallback() -> None:
    service = LLMReceptionistAnalysisService(
        provider=StaticContentLLMProvider("this is not valid json"),
    )

    result = service.analyze_message(
        ReceptionistAnalysisRequest(
            user_message="Hello",
            conversation_context={},
        ),
    )

    assert result.used_fallback is True
    assert result.failure_reason == LLMFailureReason.INVALID_JSON


def test_bedrock_invalid_json_triggers_invalid_json_fallback() -> None:
    provider, _client = build_bedrock_provider(
        response=build_valid_converse_response(content="still not json"),
    )
    service = LLMReceptionistAnalysisService(provider=provider)

    result = service.analyze_message(
        ReceptionistAnalysisRequest(
            user_message="Hello",
            conversation_context={},
        ),
    )

    assert result.used_fallback is True
    assert result.failure_reason == LLMFailureReason.INVALID_JSON


def test_bedrock_malformed_response_triggers_provider_error_fallback() -> None:
    provider, _client = build_bedrock_provider(response={"unexpected": "shape"})
    service = LLMReceptionistAnalysisService(provider=provider)

    result = service.analyze_message(
        ReceptionistAnalysisRequest(
            user_message="Hello",
            conversation_context={},
        ),
    )

    assert result.used_fallback is True
    assert result.failure_reason == LLMFailureReason.PROVIDER_ERROR


def test_bedrock_successful_response_flows_through_analysis_service() -> None:
    payload = build_receptionist_analysis_payload(intent="greeting")
    provider, _client = build_bedrock_provider(
        response=build_valid_converse_response(content=payload),
    )
    service = LLMReceptionistAnalysisService(provider=provider)

    result = service.analyze_message(
        ReceptionistAnalysisRequest(
            user_message="Hello",
            conversation_context={},
        ),
    )

    assert result.used_fallback is False
    assert result.failure_reason == LLMFailureReason.NONE
    assert result.analysis.intent == ReceptionistLLMIntent.GREETING
    assert result.input_tokens == 120
    assert result.output_tokens == 45


def test_llm_booking_confirmation_output_does_not_create_booking_or_hold() -> None:
    payload = build_receptionist_analysis_payload(
        intent="booking_confirmation",
        confidence=0.99,
    )
    provider = StaticContentLLMProvider(payload)
    service, hold_service, tracking_booking = create_chat_service_with_llm_provider(provider)

    result = service.handle_message(
        ChatMessageInput(message="Yes, please confirm my booking now."),
    )

    assert len(tracking_booking.book_calls) == 0
    assert len(hold_service.create_hold_calls) == 0
    assert result.booking_confirmed is False
    assert result.intent != ChatReceptionistIntent.BOOKING_CONFIRMED


def test_bedrock_booking_confirmation_output_does_not_create_booking_or_hold() -> None:
    payload = build_receptionist_analysis_payload(
        intent="booking_confirmation",
        confidence=0.99,
    )
    provider, _client = build_bedrock_provider(
        response=build_valid_converse_response(content=payload),
    )
    service, hold_service, tracking_booking = create_chat_service_with_llm_provider(provider)

    result = service.handle_message(
        ChatMessageInput(message="Yes, please confirm my booking now."),
    )

    assert len(tracking_booking.book_calls) == 0
    assert len(hold_service.create_hold_calls) == 0
    assert result.booking_confirmed is False


def test_emergency_output_preserves_safety_flags_in_shadow_metadata() -> None:
    payload = build_receptionist_analysis_payload(
        intent="emergency",
        confidence=0.99,
        urgency="emergency",
        safety_flags=["medical_emergency"],
    )
    provider = StaticContentLLMProvider(payload)
    service, _hold_service, _tracking_booking = create_chat_service_with_llm_provider(provider)

    result = service.handle_message(
        ChatMessageInput(message="I have chest pain and cannot breathe"),
    )

    shadow = result.assistant_message.message_metadata["llm_shadow_analysis"]
    assert shadow["intent"] == "emergency"
    assert shadow["urgency"] == "emergency"
    assert "medical_emergency" in shadow["safety_flags"]
    assert result.intent == ChatReceptionistIntent.EMERGENCY


def test_bedrock_emergency_output_preserves_safety_flags_in_shadow_metadata() -> None:
    payload = build_receptionist_analysis_payload(
        intent="emergency",
        confidence=0.99,
        urgency="emergency",
        safety_flags=["medical_emergency"],
    )
    provider, _client = build_bedrock_provider(
        response=build_valid_converse_response(content=payload),
    )
    service, _hold_service, _tracking_booking = create_chat_service_with_llm_provider(provider)

    result = service.handle_message(
        ChatMessageInput(message="I have chest pain and cannot breathe"),
    )

    shadow = result.assistant_message.message_metadata["llm_shadow_analysis"]
    assert shadow["intent"] == "emergency"
    assert "medical_emergency" in shadow["safety_flags"]


def test_chat_flow_with_fake_default_llm_still_passes() -> None:
    repository = FakeConversationRepository()
    conversations = ConversationService(repository=repository)
    scheduling = create_demo_scheduling_service()
    llm_analysis = LLMReceptionistAnalysisService(provider=FakeLLMProvider())
    slot_filling = LLMChatSlotFillingService(
        scheduling=scheduling,
        date_parser=NaturalLanguageDateParser(),
        time_preference_parser=TimePreferenceParser(),
    )
    service = create_chat_receptionist_service(
        conversations=conversations,
        scheduling=scheduling,
        llm_analysis=llm_analysis,
        slot_filling=slot_filling,
    )

    result = service.handle_message(ChatMessageInput(message="Hello"))

    assert result.intent == ChatReceptionistIntent.GREETING
    assert "llm_shadow_analysis" in result.assistant_message.message_metadata
    shadow = result.assistant_message.message_metadata["llm_shadow_analysis"]
    assert shadow["used_fallback"] is False
    assert "raw_prompt" not in shadow
    assert "raw_provider_output" not in shadow
