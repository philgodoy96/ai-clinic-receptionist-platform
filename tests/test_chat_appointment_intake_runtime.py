from __future__ import annotations

from datetime import date
from unittest.mock import patch

from app.domain.chat_turn_understanding import (
    ChatTurnIntent,
    ChatTurnUnderstandingRequest,
    ChatTurnUnderstandingResult,
    ExtractedTurnFields,
)
from app.services.chat_receptionist import (
    ChatMessageInput,
    ChatReceptionistIntent,
    ChatReceptionistService,
)
from app.services.date_parsing import FixedClock, NaturalLanguageDateParser
from app.services.fake_chat_turn_understanding_interpreter import (
    FakeChatTurnUnderstandingInterpreter,
)
from app.services.time_preferences import TimePreferenceParser
from tests.chat_booking_flow_support import conversation_with_active_hold, send_chat_messages
from tests.test_chat_receptionist_service import (
    _create_hold_service,
    create_chat_receptionist_service,
)
from tests.test_conversations import FakeConversationRepository
from tests.test_scheduling_services import (
    create_demo_scheduling_service_with_emily_july_availability,
)


class SpyChatTurnUnderstandingInterpreter:
    def __init__(self) -> None:
        self.calls: list[ChatTurnUnderstandingRequest] = []

    def interpret(
        self,
        request: ChatTurnUnderstandingRequest,
    ) -> ChatTurnUnderstandingResult:
        self.calls.append(request)
        return ChatTurnUnderstandingResult(
            intent=ChatTurnIntent.FALLBACK,
            confidence=0.2,
            reason="forced fallback",
        )


class StubChatTurnUnderstandingInterpreter:
    def __init__(self, result: ChatTurnUnderstandingResult) -> None:
        self.result = result
        self.calls: list[ChatTurnUnderstandingRequest] = []

    def interpret(
        self,
        request: ChatTurnUnderstandingRequest,
    ) -> ChatTurnUnderstandingResult:
        self.calls.append(request)
        return self.result


def _create_runtime_service(
    interpreter: object | None,
    *,
    repository: FakeConversationRepository | None = None,
) -> ChatReceptionistService:
    from app.services.conversations import ConversationService

    repository = repository or FakeConversationRepository()
    conversations = ConversationService(repository=repository)
    scheduling = create_demo_scheduling_service_with_emily_july_availability()
    date_parser = NaturalLanguageDateParser(clock=FixedClock(current_date=date(2026, 7, 1)))
    return create_chat_receptionist_service(
        conversations=conversations,
        scheduling=scheduling,
        hold_service=_create_hold_service(),
        date_parser=date_parser,
        time_preference_parser=TimePreferenceParser(),
        chat_turn_understanding_interpreter=interpreter,  # type: ignore[arg-type]
    )


def _cardiology_specialty_id(service: ChatReceptionistService) -> str:
    for specialty in service.scheduling.list_specialties():
        if specialty.name == "Cardiology":
            return str(specialty.id)
    raise AssertionError("Cardiology specialty not found")


def test_ctu_disabled_preserves_existing_deterministic_behavior() -> None:
    service = _create_runtime_service(None)
    scheduling = service.scheduling

    with patch.object(
        scheduling,
        "check_availability",
        wraps=scheduling.check_availability,
    ) as check_availability_mock:
        result = service.handle_message(
            ChatMessageInput(message="Dermatology next Monday"),
        )

    assert result.intent == ChatReceptionistIntent.AVAILABILITY_NO_SLOTS
    check_availability_mock.assert_called_once()
    chat_context = result.conversation.conversation_metadata["chat_context"]
    assert chat_context["selected_specialty_name"] == "Dermatology"
    assert chat_context["requested_date"] == "2026-07-06"


def test_ctu_noop_preserves_existing_deterministic_behavior() -> None:
    spy = SpyChatTurnUnderstandingInterpreter()
    service = _create_runtime_service(spy)
    scheduling = service.scheduling

    with patch.object(
        scheduling,
        "check_availability",
        wraps=scheduling.check_availability,
    ) as check_availability_mock:
        result = service.handle_message(
            ChatMessageInput(message="Dermatology next Monday"),
        )

    assert result.intent == ChatReceptionistIntent.AVAILABILITY_NO_SLOTS
    check_availability_mock.assert_called_once()
    assert len(spy.calls) == 1
    chat_context = result.conversation.conversation_metadata["chat_context"]
    assert chat_context["selected_specialty_name"] == "Dermatology"
    assert chat_context["requested_date"] == "2026-07-06"


def test_specialty_only_message_applies_context_and_avoids_generic_fallback() -> None:
    interpreter = StubChatTurnUnderstandingInterpreter(
        ChatTurnUnderstandingResult(
            intent=ChatTurnIntent.APPOINTMENT_REQUEST,
            confidence=0.9,
            reason="specialty request",
            extracted_fields=ExtractedTurnFields(
                specialty_raw="dermatology",
            ),
        ),
    )
    service = _create_runtime_service(interpreter)

    result = service.handle_message(
        ChatMessageInput(message="I'd like to schedule with a dermatologist"),
    )

    assert result.intent == ChatReceptionistIntent.AVAILABILITY_MISSING_DATE
    assert "YYYY-MM-DD" not in result.reply
    assert "what day works best" in result.reply.lower()
    chat_context = result.conversation.conversation_metadata["chat_context"]
    assert chat_context["selected_specialty_name"] == "Dermatology"


def test_cardiology_next_monday_applies_context_and_reaches_availability() -> None:
    interpreter = StubChatTurnUnderstandingInterpreter(
        ChatTurnUnderstandingResult(
            intent=ChatTurnIntent.APPOINTMENT_REQUEST,
            confidence=0.9,
            reason="specialty and date request",
            extracted_fields=ExtractedTurnFields(
                specialty_raw="cardiology",
                appointment_date_raw="next Monday",
            ),
        ),
    )
    service = _create_runtime_service(interpreter)
    scheduling = service.scheduling

    with patch.object(
        scheduling,
        "check_availability",
        wraps=scheduling.check_availability,
    ) as check_availability_mock:
        result = service.handle_message(
            ChatMessageInput(message="cardiology next Monday"),
        )

    check_availability_mock.assert_called_once()
    chat_context = result.conversation.conversation_metadata["chat_context"]
    assert chat_context["selected_specialty_name"] == "Cardiology"
    assert chat_context["selected_specialty_id"] == _cardiology_specialty_id(service)
    assert chat_context["requested_date"] == "2026-07-06"
    assert result.intent in {
        ChatReceptionistIntent.AVAILABILITY_RESULTS,
        ChatReceptionistIntent.AVAILABILITY_NO_SLOTS,
    }


def test_dr_reed_next_monday_applies_doctor_date_and_reaches_availability() -> None:
    interpreter = StubChatTurnUnderstandingInterpreter(
        ChatTurnUnderstandingResult(
            intent=ChatTurnIntent.APPOINTMENT_REQUEST,
            confidence=0.9,
            reason="doctor and date request",
            extracted_fields=ExtractedTurnFields(
                doctor_name_raw="Dr. Reed",
                appointment_date_raw="next Monday",
            ),
        ),
    )
    service = _create_runtime_service(interpreter)
    scheduling = service.scheduling

    with patch.object(
        scheduling,
        "check_availability",
        wraps=scheduling.check_availability,
    ) as check_availability_mock:
        result = service.handle_message(
            ChatMessageInput(message="Dr. Reed next Monday"),
        )

    check_availability_mock.assert_called_once()
    chat_context = result.conversation.conversation_metadata["chat_context"]
    assert chat_context["selected_doctor_name"] == "Dr. Michael Reed"
    assert chat_context["requested_date"] == "2026-07-06"
    assert result.intent in {
        ChatReceptionistIntent.AVAILABILITY_RESULTS,
        ChatReceptionistIntent.AVAILABILITY_NO_SLOTS,
    }


def test_unknown_doctor_returns_clarification_without_availability() -> None:
    interpreter = StubChatTurnUnderstandingInterpreter(
        ChatTurnUnderstandingResult(
            intent=ChatTurnIntent.APPOINTMENT_REQUEST,
            confidence=0.9,
            reason="unknown doctor",
            extracted_fields=ExtractedTurnFields(
                doctor_name_raw="Dr. Unknown",
            ),
        ),
    )
    service = _create_runtime_service(interpreter)
    scheduling = service.scheduling

    with patch.object(
        scheduling,
        "check_availability",
        wraps=scheduling.check_availability,
    ) as check_availability_mock:
        result = service.handle_message(
            ChatMessageInput(message="Dr. Unknown next Monday"),
        )

    assert result.intent == ChatReceptionistIntent.APPOINTMENT_REQUEST
    assert "doctor" in result.reply.lower()
    check_availability_mock.assert_not_called()
    assert "selected_doctor_id" not in result.conversation.conversation_metadata.get(
        "chat_context",
        {},
    )


def test_doctor_specialty_mismatch_returns_clarification_without_unsafe_selection() -> None:
    interpreter = StubChatTurnUnderstandingInterpreter(
        ChatTurnUnderstandingResult(
            intent=ChatTurnIntent.APPOINTMENT_REQUEST,
            confidence=0.9,
            reason="mixed provider request",
            extracted_fields=ExtractedTurnFields(
                specialty_raw="cardiology",
                doctor_name="Dr. Emily Carter",
            ),
        ),
    )
    service = _create_runtime_service(interpreter)
    scheduling = service.scheduling

    with patch.object(
        scheduling,
        "check_availability",
        wraps=scheduling.check_availability,
    ) as check_availability_mock:
        result = service.handle_message(
            ChatMessageInput(message="cardiology with Dr. Emily Carter next Monday"),
        )

    assert result.intent == ChatReceptionistIntent.APPOINTMENT_REQUEST
    assert "not in cardiology" in result.reply.lower()
    check_availability_mock.assert_not_called()
    chat_context = result.conversation.conversation_metadata.get("chat_context", {})
    assert chat_context.get("selected_specialty_id") is None
    assert chat_context.get("selected_doctor_id") is None


def test_booking_identity_flow_does_not_invoke_appointment_intake_ctu() -> None:
    repository = FakeConversationRepository()
    hold_service = _create_runtime_service(None, repository=repository)
    conversation = conversation_with_active_hold(hold_service)
    yes_result = send_chat_messages(hold_service, conversation.id, ("Yes.",))
    hold_context = yes_result.conversation.conversation_metadata["chat_context"]
    assert hold_context.get("hold_id")
    assert hold_service._booking_identity.is_active(hold_context)

    intake_service = _create_runtime_service(
        FakeChatTurnUnderstandingInterpreter(),
        repository=repository,
    )
    with patch.object(intake_service._appointment_intake, "handle") as intake_handle_mock:
        result = send_chat_messages(
            intake_service,
            conversation.id,
            ("Felipe Marques, Sep 19th 1996",),
        )

    intake_handle_mock.assert_not_called()
    assert result.intent != ChatReceptionistIntent.FALLBACK


def test_cancel_message_still_routes_through_top_level_flow_not_intake() -> None:
    spy = SpyChatTurnUnderstandingInterpreter()
    service = _create_runtime_service(spy)

    result = service.handle_message(
        ChatMessageInput(message="I need to cancel my appointment"),
    )

    assert result.intent == ChatReceptionistIntent.CANCEL_REQUEST
    assert spy.calls == []


def test_reschedule_message_still_routes_through_top_level_flow_not_intake() -> None:
    spy = SpyChatTurnUnderstandingInterpreter()
    service = _create_runtime_service(spy)

    result = service.handle_message(
        ChatMessageInput(message="I need to reschedule my appointment"),
    )

    assert result.intent == ChatReceptionistIntent.RESCHEDULE_REQUEST
    assert spy.calls == []
