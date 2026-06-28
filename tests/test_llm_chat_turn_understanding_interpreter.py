from __future__ import annotations

import json

from app.ai.chat_turn_understanding_prompt import build_chat_turn_understanding_system_prompt
from app.ai.llm_provider import LLMRequest, LLMResponse
from app.ai.prompt_versions import get_current_chat_turn_understanding_prompt_metadata
from app.domain.chat_turn_understanding import (
    ChatTurnIntent,
    ChatTurnUnderstandingRequest,
    ConversationState,
    ExpectedResponseType,
    KnownDoctor,
    KnownSpecialty,
    OfferedSlot,
)
from app.services.llm_chat_turn_understanding_interpreter import (
    LLMChatTurnUnderstandingInterpreter,
    build_chat_turn_understanding_context_snapshot,
)
from tests.llm_provider_test_helpers import RaisingLLMProvider, StaticContentLLMProvider
from tests.llm_reliability_test_helpers import (
    REPAIR_PROMPT_MARKER,
    AlwaysFailingLLMProvider,
    CountingLLMProvider,
    FailOnceThenSucceedProvider,
    SequentialCapturingContentProvider,
)

SIDE_EFFECT_FIELD_NAMES = frozenset(
    {
        "book_appointment",
        "cancel_appointment",
        "reschedule_appointment",
        "create_patient",
        "send_email",
        "execute_action",
        "tool_call",
        "tool_calls",
        "mutate_state",
    },
)

DERMATOLOGY = KnownSpecialty(id="spec-1", name="Dermatology", aliases=["derm"])


def build_chat_turn_understanding_payload(**overrides: object) -> str:
    payload: dict[str, object] = {
        "intent": "greeting",
        "confidence": 0.9,
        "reason": "User greeted the assistant.",
    }
    payload.update(overrides)
    return json.dumps(payload)


def build_interpretation_request(**overrides: object) -> ChatTurnUnderstandingRequest:
    defaults: dict[str, object] = {
        "conversation_state": ConversationState.IDLE,
        "expected_response_type": ExpectedResponseType.OPEN_TEXT,
        "latest_user_message": "Hello",
        "allowed_intents": list(ChatTurnIntent),
    }
    defaults.update(overrides)
    return ChatTurnUnderstandingRequest(**defaults)


class CapturingLLMProvider(StaticContentLLMProvider):
    def __init__(self, content: str) -> None:
        super().__init__(content)
        self.requests: list[LLMRequest] = []

    def complete(self, request: LLMRequest) -> LLMResponse:
        self.requests.append(request)
        return super().complete(request)


def build_interpreter(
    *,
    primary_provider: object,
    fallback_provider: object | None = None,
    max_primary_attempts: int = 2,
    max_fallback_attempts: int = 1,
) -> LLMChatTurnUnderstandingInterpreter:
    return LLMChatTurnUnderstandingInterpreter(
        primary_provider=primary_provider,  # type: ignore[arg-type]
        fallback_provider=fallback_provider,  # type: ignore[arg-type]
        max_primary_attempts=max_primary_attempts,
        max_fallback_attempts=max_fallback_attempts,
    )


def request_includes_repair_prompt(request: LLMRequest) -> bool:
    return any(REPAIR_PROMPT_MARKER in message.content for message in request.messages)


def context_snapshot_from_request(request: LLMRequest) -> dict[str, object]:
    for message in request.messages:
        if message.role == "system" and "Chat turn understanding context:" in message.content:
            snapshot_json = message.content.split("Chat turn understanding context:\n", 1)[1]
            parsed = json.loads(snapshot_json)
            assert isinstance(parsed, dict)
            return parsed
    raise AssertionError("context snapshot not found in request")


def test_parses_valid_provider_json_into_result() -> None:
    interpreter = build_interpreter(
        primary_provider=StaticContentLLMProvider(
            build_chat_turn_understanding_payload(intent="appointment_request"),
        ),
    )

    result = interpreter.interpret(build_interpretation_request(latest_user_message="Book me"))

    assert result.intent is ChatTurnIntent.APPOINTMENT_REQUEST
    assert result.confidence == 0.9
    assert result.reason == "User greeted the assistant."


def test_rendered_request_includes_conversation_state_and_expected_response_type() -> None:
    provider = CapturingLLMProvider(build_chat_turn_understanding_payload())
    interpreter = build_interpreter(primary_provider=provider)
    request = build_interpretation_request(
        conversation_state=ConversationState.COLLECTING_PATIENT_IDENTITY,
        expected_response_type=ExpectedResponseType.PATIENT_IDENTITY,
        latest_user_message="John Smith, Sep 19th 1996",
    )

    interpreter.interpret(request)

    assert provider.requests
    snapshot = context_snapshot_from_request(provider.requests[0])
    assert snapshot["conversation_state"] == "collecting_patient_identity"
    assert snapshot["expected_response_type"] == "patient_identity"
    assert provider.requests[0].messages[-1].content == request.latest_user_message


def test_rendered_request_includes_catalog_and_offered_slots() -> None:
    provider = CapturingLLMProvider(build_chat_turn_understanding_payload())
    interpreter = build_interpreter(primary_provider=provider)
    offered_slots = [
        OfferedSlot(reference="slot-1", start_time="2026-07-02T14:00:00"),
    ]
    known_doctors = [
        KnownDoctor(id="doc-1", full_name="Dr. Emily Carter", specialty_id="spec-1"),
    ]
    request = build_interpretation_request(
        offered_slots=offered_slots,
        known_specialties=[DERMATOLOGY],
        known_doctors=known_doctors,
    )

    interpreter.interpret(request)

    snapshot = context_snapshot_from_request(provider.requests[0])
    assert snapshot["offered_slots"] == [slot.model_dump(mode="json") for slot in offered_slots]
    assert snapshot["known_specialties"] == [DERMATOLOGY.model_dump(mode="json")]
    assert snapshot["known_doctors"] == [known_doctors[0].model_dump(mode="json")]


def test_uses_chat_turn_understanding_prompt_metadata_and_system_prompt() -> None:
    provider = CapturingLLMProvider(build_chat_turn_understanding_payload())
    interpreter = build_interpreter(primary_provider=provider)

    interpreter.interpret(build_interpretation_request())

    metadata = get_current_chat_turn_understanding_prompt_metadata()
    assert provider.requests[0].metadata["prompt_version"] == metadata.version
    assert provider.requests[0].metadata["component"] == "chat_turn_understanding"
    assert provider.requests[0].messages[0].content == build_chat_turn_understanding_system_prompt()
    assert metadata.name == "chat-turn-understanding"
    assert metadata.schema_name == "ChatTurnUnderstandingResult"


def test_invalid_json_uses_local_repair_path() -> None:
    payload = build_chat_turn_understanding_payload(intent="confirmation")
    provider = CountingLLMProvider(f"```json\n{payload}\n```")
    interpreter = build_interpreter(primary_provider=provider, max_primary_attempts=1)

    result = interpreter.interpret(build_interpretation_request(latest_user_message="Yes"))

    assert provider.call_count == 1
    assert result.intent is ChatTurnIntent.CONFIRMATION


def test_schema_invalid_output_retries_with_repair_prompt_then_falls_back() -> None:
    valid_payload = build_chat_turn_understanding_payload(intent="confirmation")
    provider = SequentialCapturingContentProvider(
        [
            '{"intent":"not_a_real_intent","confidence":0.9,"reason":"bad"}',
            valid_payload,
        ],
    )
    interpreter = build_interpreter(primary_provider=provider, max_primary_attempts=2)

    result = interpreter.interpret(build_interpretation_request(latest_user_message="Yes"))

    assert provider.call_count == 2
    assert request_includes_repair_prompt(provider.requests[1])
    assert result.intent is ChatTurnIntent.CONFIRMATION


def test_retryable_primary_provider_failure_retries_once() -> None:
    provider = FailOnceThenSucceedProvider(
        success_content=build_chat_turn_understanding_payload(intent="slot_selection"),
    )
    interpreter = build_interpreter(primary_provider=provider, max_primary_attempts=2)

    result = interpreter.interpret(
        build_interpretation_request(latest_user_message="the first one"),
    )

    assert provider.call_count == 2
    assert result.intent is ChatTurnIntent.SLOT_SELECTION


def test_fallback_provider_is_used_when_primary_exhausts_retryable_failures() -> None:
    primary = AlwaysFailingLLMProvider(message="primary timeout")
    fallback = CountingLLMProvider(
        build_chat_turn_understanding_payload(intent="availability_request"),
        name="fallback",
    )
    interpreter = build_interpreter(
        primary_provider=primary,
        fallback_provider=fallback,
        max_primary_attempts=2,
        max_fallback_attempts=1,
    )

    result = interpreter.interpret(
        build_interpretation_request(latest_user_message="Any openings?"),
    )

    assert primary.call_count == 2
    assert fallback.call_count == 1
    assert result.intent is ChatTurnIntent.AVAILABILITY_REQUEST


def test_all_providers_failing_returns_safe_fallback_result() -> None:
    interpreter = build_interpreter(
        primary_provider=AlwaysFailingLLMProvider(),
        fallback_provider=AlwaysFailingLLMProvider(message="fallback failed"),
        max_primary_attempts=2,
        max_fallback_attempts=1,
    )

    result = interpreter.interpret(build_interpretation_request())

    assert result.intent is ChatTurnIntent.FALLBACK
    assert result.confidence == 0.0
    assert result.clarification_question is not None
    assert result.reason


def test_unrepairable_malformed_output_returns_safe_fallback_result() -> None:
    interpreter = build_interpreter(
        primary_provider=StaticContentLLMProvider("this is not valid json"),
        max_primary_attempts=2,
        max_fallback_attempts=0,
    )

    result = interpreter.interpret(build_interpretation_request())

    assert result.intent is ChatTurnIntent.FALLBACK
    assert result.confidence == 0.0
    assert result.clarification_question is not None


def test_provider_side_effect_fields_are_not_present_in_final_result() -> None:
    payload = build_chat_turn_understanding_payload(
        book_appointment=True,
        cancel_appointment=True,
        send_email=True,
    )
    interpreter = build_interpreter(primary_provider=StaticContentLLMProvider(payload))

    result = interpreter.interpret(build_interpretation_request())
    dumped = result.model_dump()

    assert SIDE_EFFECT_FIELD_NAMES.isdisjoint(dumped.keys())
    assert "book_appointment" not in json.dumps(dumped)


def test_context_snapshot_builder_is_deterministic() -> None:
    request = build_interpretation_request(
        conversation_state=ConversationState.OFFERING_SLOTS,
        expected_response_type=ExpectedResponseType.SLOT_SELECTION,
        current_context={"selected_specialty": "Dermatology"},
        locale="en-US",
    )

    snapshot = build_chat_turn_understanding_context_snapshot(request)

    assert snapshot == {
        "conversation_state": "offering_slots",
        "expected_response_type": "slot_selection",
        "allowed_intents": [intent.value for intent in ChatTurnIntent],
        "current_context": {"selected_specialty": "Dermatology"},
        "locale": "en-US",
    }


def test_interpreter_does_not_mutate_input_request() -> None:
    request = build_interpretation_request(
        current_context={"selected_specialty": "Dermatology"},
        offered_slots=[OfferedSlot(reference="slot-1", start_time="2026-07-02T14:00:00")],
    )
    original_context = dict(request.current_context)
    original_slots = list(request.offered_slots)

    interpreter = build_interpreter(
        primary_provider=StaticContentLLMProvider(build_chat_turn_understanding_payload()),
    )
    interpreter.interpret(request)

    assert request.current_context == original_context
    assert request.offered_slots == original_slots


def test_provider_errors_do_not_escape_to_caller() -> None:
    interpreter = build_interpreter(primary_provider=RaisingLLMProvider())

    result = interpreter.interpret(build_interpretation_request())

    assert result.intent is ChatTurnIntent.FALLBACK
