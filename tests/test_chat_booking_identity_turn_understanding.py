from __future__ import annotations

from typing import cast
from unittest.mock import patch
from uuid import uuid4

from app.domain.chat_turn_understanding import (
    ChatTurnIntent,
    ChatTurnUnderstandingRequest,
    ChatTurnUnderstandingResult,
    ConversationState,
    ExpectedResponseType,
)
from app.domain.conversations.enums import ConversationChannel, ConversationStatus
from app.models.conversations import Conversation
from app.models.scheduling import Patient
from app.services.appointment_booking import AppointmentBookingService
from app.services.chat_booking_identity import (
    ChatBookingIdentityOrchestrator,
    ChatBookingIdentityStep,
    ParsedPatientFields,
)
from app.services.chat_receptionist import (
    ChatMessageInput,
    ChatReceptionistIntent,
    ChatReceptionistService,
)
from app.services.conversations import ConversationService
from app.services.fake_chat_turn_understanding_interpreter import (
    FakeChatTurnUnderstandingInterpreter,
)
from tests.chat_booking_flow_support import (
    NEW_PATIENT_IDENTITY_STEPS,
    conversation_with_active_hold,
    send_chat_messages,
)
from tests.test_chat_receptionist_service import (
    TrackingAppointmentBookingService,
    _create_hold_service,
    create_appointment_booking_service_for_scheduling,
    create_chat_receptionist_service,
    create_patient_identity_resolution_for_scheduling,
)
from tests.test_conversations import FakeConversationRepository
from tests.test_scheduling_services import (
    create_demo_scheduling_service_with_emily_july_availability,
    create_patient,
)


class SpyChatTurnUnderstandingInterpreter:
    def __init__(self, *, inner: FakeChatTurnUnderstandingInterpreter | None = None) -> None:
        self.inner = inner or FakeChatTurnUnderstandingInterpreter()
        self.calls: list[ChatTurnUnderstandingRequest] = []

    def interpret(
        self,
        request: ChatTurnUnderstandingRequest,
    ) -> ChatTurnUnderstandingResult:
        self.calls.append(request)
        return self.inner.interpret(request)


class RaisingChatTurnUnderstandingInterpreter:
    def interpret(
        self,
        request: ChatTurnUnderstandingRequest,
    ) -> ChatTurnUnderstandingResult:
        del request
        raise RuntimeError("simulated interpreter failure")


class FallbackChatTurnUnderstandingInterpreter:
    def interpret(
        self,
        request: ChatTurnUnderstandingRequest,
    ) -> ChatTurnUnderstandingResult:
        del request
        return ChatTurnUnderstandingResult(
            intent=ChatTurnIntent.FALLBACK,
            confidence=0.2,
            reason="forced fallback",
            clarification_question="Could you please clarify what you'd like to do?",
        )


def _create_service_with_interpreter(
    interpreter: object | None,
    *,
    patients: list[Patient] | None = None,
) -> tuple[ChatReceptionistService, TrackingAppointmentBookingService]:
    repository = FakeConversationRepository()
    conversations = ConversationService(repository=repository)
    hold_service = _create_hold_service()
    scheduling = create_demo_scheduling_service_with_emily_july_availability(
        patients=patients or [],
    )
    inner_booking = create_appointment_booking_service_for_scheduling(
        scheduling,
        hold_service,
    )
    tracking_booking = TrackingAppointmentBookingService(inner_booking)
    service = create_chat_receptionist_service(
        conversations=conversations,
        scheduling=scheduling,
        hold_service=hold_service,
        appointment_booking=cast(AppointmentBookingService, tracking_booking),
        chat_turn_understanding_interpreter=interpreter,  # type: ignore[arg-type]
    )
    return service, tracking_booking


def _conversation_at_existing_identity_step(
    service: ChatReceptionistService,
) -> Conversation:
    conversation = conversation_with_active_hold(service)
    send_chat_messages(service, conversation.id, ("Yes.",))
    return conversation


def test_existing_patient_identity_accepts_natural_dob_via_ctu() -> None:
    interpreter = FakeChatTurnUnderstandingInterpreter()
    service, _tracking = _create_service_with_interpreter(interpreter)
    conversation = _conversation_at_existing_identity_step(service)

    result = service.handle_message(
        ChatMessageInput(
            message="John Smith, Sep 19th 1996",
            conversation_id=conversation.id,
        ),
    )

    context = result.conversation.conversation_metadata["chat_context"]
    identity = context["patient_identity"]
    assert identity["full_name"] == "John Smith"
    assert identity["date_of_birth"] == "1996-09-19"


def test_existing_patient_identity_still_accepts_iso_dob_without_ctu() -> None:
    service, _tracking = _create_service_with_interpreter(None)
    conversation = _conversation_at_existing_identity_step(service)

    result = service.handle_message(
        ChatMessageInput(
            message="John Miller, 1985-04-12",
            conversation_id=conversation.id,
        ),
    )

    context = result.conversation.conversation_metadata["chat_context"]
    identity = context["patient_identity"]
    assert identity["full_name"] == "John Miller"
    assert identity["date_of_birth"] == "1985-04-12"


def test_new_patient_bundled_answer_accepts_typed_email_without_confirmation() -> None:
    interpreter = FakeChatTurnUnderstandingInterpreter()
    service, tracking = _create_service_with_interpreter(interpreter)
    conversation = conversation_with_active_hold(service)

    result = service.handle_message(
        ChatMessageInput(
            message=(
                "No, I'm new. John Smith, born Sep 19th 1996, "
                "email john.smith@example.com"
            ),
            conversation_id=conversation.id,
        ),
    )

    context = result.conversation.conversation_metadata["chat_context"]
    assert context["patient_seen_before"] is False
    assert context["patient_identity"]["full_name"] == "John Smith"
    assert context["patient_identity"]["date_of_birth"] == "1996-09-19"
    # Written chat accepts the typed email immediately: no confirmation turn.
    assert context["patient_identity"]["email"] == "john.smith@example.com"
    assert context["confirmed_booking_email"] == "john.smith@example.com"
    assert "pending_confirmation_email" not in context
    assert context["booking_identity_step"] == (
        ChatBookingIdentityStep.AWAIT_FINAL_BOOKING_CONFIRMATION.value
    )
    assert "is that correct" not in result.reply.lower()
    assert "john.smith@example.com" in result.reply.lower()
    # Email acceptance still requires a final booking confirmation before booking.
    assert tracking.book_calls == []


def test_ambiguous_dob_does_not_store_dob_or_resolve_patient() -> None:
    interpreter = FakeChatTurnUnderstandingInterpreter()
    service, _tracking = _create_service_with_interpreter(interpreter)
    conversation = _conversation_at_existing_identity_step(service)

    with patch.object(
        service._booking_identity.patient_identity_resolution,
        "resolve",
    ) as resolve_mock:
        result = service.handle_message(
            ChatMessageInput(
                message="John Smith, 09/10/1996",
                conversation_id=conversation.id,
            ),
        )

        assert resolve_mock.call_count == 0

    context = result.conversation.conversation_metadata["chat_context"]
    identity = context.get("patient_identity", {})
    assert identity.get("full_name") == "John Smith"
    assert "date_of_birth" not in identity
    assert "september" in result.reply.lower() or "october" in result.reply.lower()


def test_interpreter_exception_falls_back_to_deterministic_iso_dob() -> None:
    service, _tracking = _create_service_with_interpreter(
        RaisingChatTurnUnderstandingInterpreter(),
    )
    conversation = _conversation_at_existing_identity_step(service)

    result = service.handle_message(
        ChatMessageInput(
            message="John Miller, 1985-04-12",
            conversation_id=conversation.id,
        ),
    )

    context = result.conversation.conversation_metadata["chat_context"]
    identity = context["patient_identity"]
    assert identity["full_name"] == "John Miller"
    assert identity["date_of_birth"] == "1985-04-12"


def test_interpreter_fallback_uses_deterministic_parser() -> None:
    service, _tracking = _create_service_with_interpreter(
        FallbackChatTurnUnderstandingInterpreter(),
    )
    conversation = _conversation_at_existing_identity_step(service)

    result = service.handle_message(
        ChatMessageInput(
            message="John Miller, 1985-04-12",
            conversation_id=conversation.id,
        ),
    )

    context = result.conversation.conversation_metadata["chat_context"]
    identity = context["patient_identity"]
    assert identity["full_name"] == "John Miller"
    assert identity["date_of_birth"] == "1985-04-12"


def test_interpreter_not_called_outside_identity_intake_steps() -> None:
    spy = SpyChatTurnUnderstandingInterpreter()
    service, _tracking = _create_service_with_interpreter(spy)
    conversation = conversation_with_active_hold(service)

    send_chat_messages(
        service,
        conversation.id,
        NEW_PATIENT_IDENTITY_STEPS,
    )
    final_confirm_calls = len(spy.calls)
    service.handle_message(
        ChatMessageInput(message="Yes", conversation_id=conversation.id),
    )

    assert final_confirm_calls == len(spy.calls)
    assert all(
        call.current_context.get("booking_identity_step")
        != ChatBookingIdentityStep.AWAIT_FINAL_BOOKING_CONFIRMATION.value
        for call in spy.calls
    )


def test_existing_identity_happy_path_still_passes_with_ctu_enabled() -> None:
    john = create_patient()
    interpreter = FakeChatTurnUnderstandingInterpreter()
    service, tracking = _create_service_with_interpreter(interpreter, patients=[john])
    conversation = conversation_with_active_hold(service)

    send_chat_messages(
        service,
        conversation.id,
        ("Yes.", "John Miller, 1985-04-12", "john.miller@example.test"),
    )
    booked = service.handle_message(
        ChatMessageInput(message="Yes", conversation_id=conversation.id),
    )

    assert booked.intent == ChatReceptionistIntent.BOOKING_CONFIRMED
    assert booked.booking_confirmed is True
    assert len(tracking.book_calls) == 1


def test_orchestrator_builds_safe_request_context() -> None:
    interpreter = SpyChatTurnUnderstandingInterpreter()
    resolution = create_patient_identity_resolution_for_scheduling(
        create_demo_scheduling_service_with_emily_july_availability(),
    )
    orchestrator = ChatBookingIdentityOrchestrator(
        patient_identity_resolution=resolution,
        chat_turn_understanding_interpreter=interpreter,
    )
    conversation = Conversation(
        id=uuid4(),
        channel=ConversationChannel.CHAT,
        status=ConversationStatus.ACTIVE,
        conversation_metadata={},
    )
    merged_context = {
        "booking_identity_step": ChatBookingIdentityStep.COLLECT_EXISTING_IDENTITY.value,
        "hold_id": "hold-1",
        "patient_seen_before": True,
        "patient_identity": {"full_name": "Jane Doe"},
    }

    orchestrator.handle(
        message="John Smith, Sep 19th 1996",
        conversation=conversation,
        merged_context=merged_context,
        context_updates={},
        parse_patient_fields=lambda _message, booking_context=False: ParsedPatientFields(),
        format_missing_identity_fields=lambda _fields: "",
        hold_id="hold-1",
    )

    assert len(interpreter.calls) == 1
    request = interpreter.calls[0]
    assert request.conversation_state is ConversationState.COLLECTING_PATIENT_IDENTITY
    assert request.expected_response_type is ExpectedResponseType.PATIENT_IDENTITY
    assert request.current_context["booking_identity_step"] == (
        ChatBookingIdentityStep.COLLECT_EXISTING_IDENTITY.value
    )
    assert request.current_context["hold_active"] is True
    assert request.current_context["patient_seen_before"] is True
    assert "raw_prompt" not in request.current_context


def test_orchestrator_skips_ctu_when_interpreter_not_configured() -> None:
    resolution = create_patient_identity_resolution_for_scheduling(
        create_demo_scheduling_service_with_emily_july_availability(),
    )
    orchestrator = ChatBookingIdentityOrchestrator(
        patient_identity_resolution=resolution,
        chat_turn_understanding_interpreter=None,
    )
    conversation = Conversation(
        id=uuid4(),
        channel=ConversationChannel.CHAT,
        status=ConversationStatus.ACTIVE,
        conversation_metadata={},
    )
    merged_context = {
        "booking_identity_step": ChatBookingIdentityStep.COLLECT_EXISTING_IDENTITY.value,
        "hold_id": "hold-1",
    }
    parser_called = False

    def parse_patient_fields(message: str, *, booking_context: bool = False) -> object:
        nonlocal parser_called
        parser_called = True
        from app.services.chat_booking_identity import ParsedPatientFields as IdentityFields

        return IdentityFields(
            full_name="John Miller",
            date_of_birth="1985-04-12",
        )

    orchestrator.handle(
        message="John Miller, 1985-04-12",
        conversation=conversation,
        merged_context=merged_context,
        context_updates={},
        parse_patient_fields=parse_patient_fields,
        format_missing_identity_fields=lambda _fields: "",
        hold_id="hold-1",
    )

    assert parser_called is True
