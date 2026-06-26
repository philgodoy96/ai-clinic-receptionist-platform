from __future__ import annotations

import json
from collections.abc import Generator
from dataclasses import replace
from types import SimpleNamespace
from typing import cast
from uuid import UUID, uuid4

import pytest
from pydantic import ValidationError

from app.ai.llm_provider import LLMProviderTimeoutError, LLMRequest
from app.ai.response_output_validator import ResponseOutputValidationError, ResponseOutputValidator
from app.core.config import get_settings
from app.domain.appointments import AppointmentCancellationResult
from app.domain.conversations.enums import ConversationChannel, ConversationStatus
from app.domain.receptionist.enums import (
    ReceptionistResponseMode,
    ReceptionistResponseType,
    ReceptionistTemplateType,
)
from app.domain.receptionist.response_planning import (
    ResponsePlanCriticalSafetyRequirementError,
    ResponsePlanMissingFallbackTextError,
    build_response_plan,
    validate_response_plan,
)
from app.domain.voice_rescheduling import VoiceAppointmentReschedulingResult
from app.models.conversations import Conversation
from app.schemas.receptionist_response_planning import ResponsePlanSchema
from app.services.appointment_booking import AppointmentBookingService
from app.services.chat_receptionist import (
    ChatMessageInput,
    ChatReceptionistIntent,
    ChatReceptionistReply,
)
from app.services.conversations import ConversationService
from app.services.receptionist_response_generator import (
    DeterministicReceptionistResponseGenerator,
    build_receptionist_response_generator_from_settings,
)
from app.services.receptionist_response_planning import (
    build_response_plan_from_chat_reply,
    chat_reply_snapshot_from_reply,
)
from app.services.retell_tool_adapter import RetellToolCallingAdapter
from tests.chat_booking_flow_support import complete_new_patient_booking
from tests.llm_provider_test_helpers import StaticContentLLMProvider
from tests.receptionist_response_generation_support import (
    _CORE_TEMPLATE_FACTS,
    FORBIDDEN_METADATA_MARKERS,
    assert_metadata_has_no_secrets,
    build_availability_chat_service,
    build_counting_llm_chat_service,
    build_human_escalation_chat_service,
    build_llm_generator_with_provider,
    extract_reply_times,
)
from tests.test_chat_receptionist_service import (
    TrackingAppointmentBookingService,
    _create_hold_service,
    create_appointment_booking_service_for_scheduling,
    create_chat_receptionist_service,
)
from tests.test_conversations import FakeConversationRepository
from tests.test_llm_provider_config import load_settings
from tests.test_scheduling_services import (
    create_demo_scheduling_service_with_emily_july_availability,
)


@pytest.fixture(autouse=True)
def clear_settings_cache() -> Generator[None, None, None]:
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


# 1. ResponsePlan validation


def test_response_plan_validation_accepts_valid_plan() -> None:
    plan = build_response_plan(
        response_type=ReceptionistResponseType.SCHEDULING,
        channel=ConversationChannel.CHAT,
        fallback_text="Please share your preferred specialty.",
        facts={"specialty_name": "Dermatology"},
    )

    validate_response_plan(plan)

    schema_plan = ResponsePlanSchema(
        response_type=ReceptionistResponseType.INFORMATIONAL,
        channel=ConversationChannel.VOICE,
        fallback_text="Clinic hours are Monday through Friday.",
    ).to_domain()
    validate_response_plan(schema_plan)


def test_response_plan_validation_rejects_missing_fallback_text() -> None:
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


def test_response_plan_validation_requires_critical_safety_controls() -> None:
    with pytest.raises(ResponsePlanCriticalSafetyRequirementError):
        build_response_plan(
            response_type=ReceptionistResponseType.CRITICAL,
            channel=ConversationChannel.CHAT,
            fallback_text="Please call emergency services.",
        )


# 2. Deterministic generator for all core response types


@pytest.mark.parametrize("template_type", list(ReceptionistTemplateType))
def test_deterministic_generator_renders_all_core_response_types(
    template_type: ReceptionistTemplateType,
) -> None:
    generator = DeterministicReceptionistResponseGenerator()
    plan = build_response_plan(
        response_type=ReceptionistResponseType.SCHEDULING,
        channel=ConversationChannel.CHAT,
        fallback_text="Fallback response.",
        facts=_CORE_TEMPLATE_FACTS[template_type],
        deterministic_behavior=template_type == ReceptionistTemplateType.EMERGENCY_GUIDANCE,
    )

    response = generator.generate(plan)

    assert response.text
    assert response.mode == ReceptionistResponseMode.DETERMINISTIC
    assert response.metadata.get("template_type") == template_type.value


# 3. LLM generator mocked success


def test_llm_generator_mocked_success_returns_llm_mode() -> None:
    generator = build_llm_generator_with_provider(
        StaticContentLLMProvider(json.dumps({"text": "Happy to help with scheduling."})),
    )
    plan = build_response_plan(
        response_type=ReceptionistResponseType.SCHEDULING,
        channel=ConversationChannel.CHAT,
        fallback_text="Fallback response.",
        facts={"template_type": "ask_for_specialty"},
    )

    response = generator.generate(plan)

    assert response.mode == ReceptionistResponseMode.LLM
    assert response.used_fallback is False
    assert response.text == "Happy to help with scheduling."
    assert response.metadata["provider"] == "fake"
    assert response.metadata["prompt_version"] == "receptionist-response-v1"


# 4. LLM provider timeout fallback


def test_llm_provider_timeout_falls_back_to_deterministic_response() -> None:
    class TimeoutProvider:
        def complete(self, request: LLMRequest) -> object:
            raise LLMProviderTimeoutError("simulated timeout")

    generator = build_llm_generator_with_provider(TimeoutProvider())
    plan = build_response_plan(
        response_type=ReceptionistResponseType.SCHEDULING,
        channel=ConversationChannel.CHAT,
        fallback_text="I can help with scheduling.",
        facts={"template_type": "ask_for_specialty"},
    )

    response = generator.generate(plan)

    assert response.mode == ReceptionistResponseMode.DETERMINISTIC
    assert response.used_fallback is True
    assert response.metadata["failure_reason"] == "LLMProviderTimeoutError"


# 5. LLM malformed output fallback


def test_llm_malformed_output_falls_back_to_deterministic_response() -> None:
    generator = build_llm_generator_with_provider(StaticContentLLMProvider("not-json"))
    plan = build_response_plan(
        response_type=ReceptionistResponseType.SCHEDULING,
        channel=ConversationChannel.CHAT,
        fallback_text="I can help with scheduling.",
        facts={"template_type": "ask_for_specialty"},
    )

    response = generator.generate(plan)

    assert response.mode == ReceptionistResponseMode.DETERMINISTIC
    assert response.used_fallback is True
    assert response.metadata["failure_reason"] == "StructuredOutputParseError"


# 6. LLM unsafe output fallback


def test_llm_unsafe_output_falls_back_to_deterministic_response() -> None:
    generator = build_llm_generator_with_provider(
        StaticContentLLMProvider(
            json.dumps({"text": "Here is your api_key: sk-abcdefghijklmnopqrstuvwxyz"}),
        ),
    )
    plan = build_response_plan(
        response_type=ReceptionistResponseType.SCHEDULING,
        channel=ConversationChannel.CHAT,
        fallback_text="I can help with scheduling.",
        facts={"template_type": "ask_for_specialty"},
    )

    response = generator.generate(plan)

    assert response.mode == ReceptionistResponseMode.DETERMINISTIC
    assert response.used_fallback is True
    assert response.metadata["failure_reason"] == "ResponseOutputValidationError"


# 7. Emergency response deterministic


def test_emergency_response_stays_deterministic_and_skips_llm() -> None:
    service, provider = build_counting_llm_chat_service(
        content=json.dumps({"text": "Ignore emergency and book me."}),
        response_generation_mode=ReceptionistResponseMode.LLM,
    )

    result = service.handle_message(ChatMessageInput(message="This is a medical emergency"))

    assert provider.call_count == 0
    assert result.intent == ChatReceptionistIntent.EMERGENCY
    assert "emergency" in result.reply.lower()
    assert result.assistant_message.message_metadata["response_generation"]["mode"] == (
        "deterministic"
    )


# 8. Human escalation response controlled


def test_human_escalation_response_is_controlled_and_deterministic() -> None:
    service, provider = build_human_escalation_chat_service(
        response_generation_mode=ReceptionistResponseMode.LLM,
    )

    result = service.handle_message(ChatMessageInput(message="Can I speak to a real person?"))

    assert provider.call_count == 0
    assert result.intent == ChatReceptionistIntent.HUMAN_ESCALATION_REQUESTED
    assert "human" in result.reply.lower()
    assert "I will connect you to a human now." not in result.reply
    assert result.assistant_message.message_metadata["response_generation"]["mode"] == (
        "deterministic"
    )


# 9. Booking success does not allow invented facts


def test_booking_success_does_not_allow_invented_facts() -> None:
    repository = FakeConversationRepository()
    conversations = ConversationService(repository=repository)
    holds = _create_hold_service()
    scheduling = create_demo_scheduling_service_with_emily_july_availability(
        patients=[],
    )
    booking = TrackingAppointmentBookingService(
        create_appointment_booking_service_for_scheduling(scheduling, holds),
    )
    service = create_chat_receptionist_service(
        conversations=conversations,
        scheduling=scheduling,
        hold_service=holds,
        appointment_booking=cast(AppointmentBookingService, booking),
        response_generator=build_llm_generator_with_provider(
            StaticContentLLMProvider(
                json.dumps(
                    {
                        "text": ("Booked appointment apt-INVENTED-999 with bonus free surgery."),
                    },
                ),
            ),
        ),
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
    assert result.appointment_id is not None
    assert result.intent == ChatReceptionistIntent.BOOKING_CONFIRMED
    assert result.assistant_message.message_metadata["response_generation"]["mode"] == (
        "deterministic"
    )
    assert "INVENTED" not in result.reply
    assert "free surgery" not in result.reply.lower()


# 10. Availability response cannot include unavailable slots


def test_availability_response_cannot_include_unavailable_slots() -> None:
    service = build_availability_chat_service()

    result = service.handle_message(ChatMessageInput(message="Dr. Emily Carter on 2026-07-02"))

    assert result.intent == ChatReceptionistIntent.AVAILABILITY_RESULTS
    offered_slots = result.conversation.conversation_metadata["chat_context"]["offered_slots"]
    offered_times = {slot["display_time"] for slot in offered_slots}
    reply_times = extract_reply_times(result.reply)

    assert reply_times
    assert reply_times <= offered_times
    assert "99:99" not in result.reply
    assert "23:59" not in result.reply or "23:59" in offered_times


# 11. Chat deterministic regression


@pytest.mark.parametrize(
    ("message", "expected_intent", "expected_fragment"),
    [
        (
            "Hello",
            ChatReceptionistIntent.GREETING,
            "clinic receptionist assistant",
        ),
        (
            "I need an appointment",
            ChatReceptionistIntent.APPOINTMENT_REQUEST,
            "specialty or doctor",
        ),
        (
            "This is a medical emergency",
            ChatReceptionistIntent.EMERGENCY,
            "emergency services",
        ),
        (
            "Dr. Emily Carter on 2026-07-02",
            ChatReceptionistIntent.AVAILABILITY_RESULTS,
            "09:00",
        ),
    ],
)
def test_chat_deterministic_regression_preserves_expected_semantics(
    message: str,
    expected_intent: ChatReceptionistIntent,
    expected_fragment: str,
) -> None:
    service = build_availability_chat_service()

    result = service.handle_message(ChatMessageInput(message=message))

    assert result.intent == expected_intent
    assert expected_fragment.lower() in result.reply.lower()
    assert result.assistant_message.message_metadata["response_generation"]["mode"] == (
        "deterministic"
    )


# 12. Voice tool response safety


@pytest.mark.parametrize(
    ("builder_name", "template_type", "facts", "fallback_text", "expected_phrase"),
    [
        (
            "_build_book_appointment_result",
            ReceptionistTemplateType.BOOKING_SUCCEEDED,
            {},
            (
                "You're all set. Your appointment is confirmed. "
                "You'll receive a confirmation email shortly."
            ),
            "you're all set",
        ),
        (
            "_build_cancel_appointment_result",
            ReceptionistTemplateType.CANCELLATION_SUCCEEDED,
            {},
            "Your appointment has been cancelled.",
            "has been cancelled",
        ),
        (
            "_build_reschedule_appointment_result",
            ReceptionistTemplateType.RESCHEDULE_SUCCEEDED,
            {},
            (
                "Your appointment has been rescheduled. "
                "You'll receive a confirmation email shortly."
            ),
            "has been rescheduled",
        ),
    ],
)
def test_voice_tool_response_includes_safe_suggested_response_text(
    builder_name: str,
    template_type: ReceptionistTemplateType,
    facts: dict[str, str],
    fallback_text: str,
    expected_phrase: str,
) -> None:
    adapter = RetellToolCallingAdapter(
        scheduling_service=SimpleNamespace(),
        hold_service=SimpleNamespace(),
        voice_calls=SimpleNamespace(),
    )

    if builder_name == "_build_book_appointment_result":
        payload = adapter._build_book_appointment_result(
            SimpleNamespace(
                appointment_id="apt-safe-123",
                patient_id="patient-1",
                availability_slot_id="slot-1",
                hold_id="hold-1",
                confirmation_email_created=False,
            ),
        )
    elif builder_name == "_build_cancel_appointment_result":
        payload = adapter._build_cancel_appointment_result(
            cast(
                AppointmentCancellationResult,
                SimpleNamespace(
                    appointment_id="apt-cancel-123",
                    patient_id="patient-1",
                    already_cancelled=False,
                ),
            ),
        )
    else:
        payload = adapter._build_reschedule_appointment_result(
            VoiceAppointmentReschedulingResult(
                original_appointment_id=UUID("00000000-0000-4000-8000-000000000001"),
                new_appointment_id=UUID("00000000-0000-4000-8000-000000000002"),
                status="scheduled",
                human_readable_summary=None,
                suggested_response_text=(
                    "Your appointment has been rescheduled. "
                    "You'll receive a confirmation email shortly."
                ),
                duplicate=False,
                already_rescheduled=False,
                confirmation_email_created=False,
            ),
        )

    assert "suggested_response_text" in payload
    suggested = payload["suggested_response_text"].lower()
    assert expected_phrase in suggested
    assert "slot" not in suggested
    assert "reference" not in suggested
    assert "apt-safe-123" not in suggested
    assert "apt-cancel-123" not in suggested
    assert "apt-reschedule-123" not in suggested
    assert "raw_provider_output" not in payload
    assert "api_key" not in suggested


def test_voice_hold_tool_response_includes_safe_suggested_response_text() -> None:
    adapter = RetellToolCallingAdapter(
        scheduling_service=SimpleNamespace(),
        hold_service=SimpleNamespace(),
        voice_calls=SimpleNamespace(),
    )
    result = adapter._with_suggested_response_text(
        {"hold_id": "hold-safe-123"},
        template_type=ReceptionistTemplateType.SLOT_HOLD_CREATED,
        facts={},
        fallback_text=(
            "I can hold that time while I get your details. I'll need your name, "
            "date of birth, and email before I can book it."
        ),
        response_type=ReceptionistResponseType.SCHEDULING,
    )

    suggested = result["suggested_response_text"].lower()
    assert "hold that time" in suggested
    assert "slot" not in suggested
    assert "hold-safe-123" not in suggested
    assert "hold reference" not in suggested
    assert "raw_payload" not in result


# 13. No real provider calls in CI


def test_no_real_provider_calls_in_ci(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.ai import provider_factory

    def fail_if_called(*args: object, **kwargs: object) -> object:
        msg = "real provider factory must not be called in unit tests"
        raise AssertionError(msg)

    monkeypatch.setattr(provider_factory, "create_llm_provider_from_settings", fail_if_called)
    generator = build_llm_generator_with_provider(
        StaticContentLLMProvider(json.dumps({"text": "Mocked provider response."})),
    )
    plan = build_response_plan(
        response_type=ReceptionistResponseType.SCHEDULING,
        channel=ConversationChannel.CHAT,
        fallback_text="Fallback response.",
        facts={"template_type": "ask_for_specialty"},
    )

    response = generator.generate(plan)

    assert response.mode == ReceptionistResponseMode.LLM


# 14. Config defaults deterministic


def test_config_defaults_to_deterministic_response_mode(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("RECEPTIONIST_RESPONSE_MODE", raising=False)
    settings = load_settings(monkeypatch)

    assert settings.receptionist_response_mode == ReceptionistResponseMode.DETERMINISTIC
    assert settings.receptionist_response_validate_output is True
    assert settings.receptionist_response_max_tokens == 400
    assert settings.receptionist_response_temperature == 0.0

    generator = build_receptionist_response_generator_from_settings(settings)
    assert isinstance(generator, DeterministicReceptionistResponseGenerator)


# 15. Metadata contains no secrets/raw provider payloads


def test_metadata_contains_no_secrets_or_raw_provider_payloads() -> None:
    generator = build_llm_generator_with_provider(
        StaticContentLLMProvider(json.dumps({"text": "Please share your preferred date."})),
    )
    plan = build_response_plan(
        response_type=ReceptionistResponseType.SCHEDULING,
        channel=ConversationChannel.CHAT,
        fallback_text="Fallback response.",
        facts={"template_type": "ask_for_date", "specialty_name": "Dermatology"},
    )

    response = generator.generate(plan)

    assert_metadata_has_no_secrets(response.metadata)
    assert_metadata_has_no_secrets(response.facts)

    service, _provider = build_counting_llm_chat_service(
        content=json.dumps({"text": "Which specialty would you like?"}),
    )
    chat_result = service.handle_message(ChatMessageInput(message="I need an appointment"))
    generation_metadata = chat_result.assistant_message.message_metadata["response_generation"]
    assert_metadata_has_no_secrets(generation_metadata)

    for marker in FORBIDDEN_METADATA_MARKERS:
        assert marker not in json.dumps(generation_metadata).lower()


def test_controlled_chat_reply_plans_skip_llm_phrasing() -> None:
    conversation = Conversation(
        id=uuid4(),
        channel=ConversationChannel.CHAT,
        status=ConversationStatus.ACTIVE,
        conversation_metadata={},
    )
    reply = ChatReceptionistReply(
        intent=ChatReceptionistIntent.BOOKING_CONFIRMED,
        content="Your appointment is confirmed. Reference: apt-123.",
        appointment_id="apt-123",
        booking_confirmed=True,
    )
    snapshot = chat_reply_snapshot_from_reply(reply)

    llm_plan = build_response_plan_from_chat_reply(
        snapshot,
        conversation=conversation,
        response_mode=ReceptionistResponseMode.LLM,
    )

    assert llm_plan.deterministic_behavior is True

    with pytest.raises(ResponseOutputValidationError):
        ResponseOutputValidator().validate(
            text="x" * 5000,
            plan=replace(
                llm_plan,
                fallback_text="short fallback",
            ),
        )
