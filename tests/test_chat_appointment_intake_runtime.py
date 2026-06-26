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
from tests.clinic_time_test_support import make_test_clinic_time_service
from tests.test_chat_receptionist_service import (
    _create_hold_service,
    create_chat_receptionist_service,
)
from tests.test_conversations import FakeConversationRepository
from tests.test_scheduling_services import (
    create_availability_slot,
    create_demo_scheduling_service_with_emily_july_availability,
    create_service,
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
    clinic_time_service: object | None = None,
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
        clinic_time_service=clinic_time_service,  # type: ignore[arg-type]
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


def test_specialty_only_message_applies_context_and_returns_earliest_availability() -> None:
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
    service = _create_runtime_service(
        interpreter,
        clinic_time_service=make_test_clinic_time_service(),
    )
    scheduling = service.scheduling

    with patch.object(
        scheduling,
        "check_availability_with_status",
        wraps=scheduling.check_availability_with_status,
    ) as doctor_availability_mock:
        result = service.handle_message(
            ChatMessageInput(message="I'd like to schedule with a dermatologist"),
        )

    doctor_availability_mock.assert_called_once()
    assert result.intent == ChatReceptionistIntent.AVAILABILITY_RESULTS
    assert "YYYY-MM-DD" not in result.reply
    assert "Dr. Emily Carter" in result.reply
    assert "which time works better" in result.reply.lower()
    chat_context = result.conversation.conversation_metadata["chat_context"]
    assert chat_context["selected_specialty_name"] == "Dermatology"
    assert chat_context.get("offered_slots")


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


def _reed_doctor_id(service: ChatReceptionistService) -> str:
    for doctor in service.scheduling.list_doctors():
        if doctor.full_name == "Dr. Michael Reed":
            return str(doctor.id)
    raise AssertionError("Dr. Michael Reed not found")


def test_specialty_doctor_list_stores_offered_doctors_in_chat_context() -> None:
    service = _create_runtime_service(None)

    result = service.handle_message(ChatMessageInput(message="cardiology"))

    assert result.intent == ChatReceptionistIntent.SPECIALTY_DOCTORS
    assert "Dr. Michael Reed" in result.reply
    chat_context = result.conversation.conversation_metadata["chat_context"]
    assert chat_context["selected_specialty_name"] == "Cardiology"
    assert chat_context["selected_specialty_id"] == _cardiology_specialty_id(service)
    offered_doctors = chat_context["offered_doctors"]
    assert len(offered_doctors) == 1
    assert offered_doctors[0] == {
        "reference": "doctor-1",
        "doctor_id": _reed_doctor_id(service),
        "doctor_name": "Dr. Michael Reed",
        "specialty_id": _cardiology_specialty_id(service),
        "specialty_name": "Cardiology",
    }


def test_offered_doctor_selection_sets_doctor_and_asks_for_date() -> None:
    interpreter = StubChatTurnUnderstandingInterpreter(
        ChatTurnUnderstandingResult(
            intent=ChatTurnIntent.FALLBACK,
            confidence=0.2,
            reason="unused for first turn",
        ),
    )
    service = _create_runtime_service(interpreter)
    scheduling = service.scheduling

    first = service.handle_message(ChatMessageInput(message="cardiology"))
    conversation_id = first.conversation.id

    interpreter.result = ChatTurnUnderstandingResult(
        intent=ChatTurnIntent.APPOINTMENT_REQUEST,
        confidence=0.9,
        reason="offered doctor selection",
        extracted_fields=ExtractedTurnFields(
            doctor_name_raw="Dr. Reed",
        ),
    )

    with patch.object(
        scheduling,
        "check_availability_with_status",
        wraps=scheduling.check_availability_with_status,
    ) as check_availability_mock:
        second = service.handle_message(
            ChatMessageInput(
                message="It can be Dr. Reed",
                conversation_id=conversation_id,
            ),
        )

    check_availability_mock.assert_called_once()
    assert second.intent == ChatReceptionistIntent.AVAILABILITY_NO_SLOTS
    assert "not seeing openings" in second.reply.lower()
    assert "what day works best" not in second.reply.lower()
    assert "YYYY-MM-DD" not in second.reply
    assert "specialty or doctor" not in second.reply.lower()
    chat_context = second.conversation.conversation_metadata["chat_context"]
    assert chat_context["selected_doctor_name"] == "Dr. Michael Reed"
    assert chat_context["selected_doctor_id"] == _reed_doctor_id(service)


def test_dermatology_offered_doctor_selection_is_contextual() -> None:
    interpreter = StubChatTurnUnderstandingInterpreter(
        ChatTurnUnderstandingResult(
            intent=ChatTurnIntent.FALLBACK,
            confidence=0.2,
            reason="unused for first turn",
        ),
    )
    service = _create_runtime_service(interpreter)
    scheduling = service.scheduling

    first = service.handle_message(
        ChatMessageInput(message="I need a dermatologist"),
    )
    conversation_id = first.conversation.id
    assert first.intent == ChatReceptionistIntent.SPECIALTY_DOCTORS
    offered_doctors = first.conversation.conversation_metadata["chat_context"]["offered_doctors"]
    assert offered_doctors[0]["doctor_name"] == "Dr. Emily Carter"

    interpreter.result = ChatTurnUnderstandingResult(
        intent=ChatTurnIntent.APPOINTMENT_REQUEST,
        confidence=0.9,
        reason="offered dermatology doctor selection",
        extracted_fields=ExtractedTurnFields(
            doctor_name_raw="Dr. Emily",
        ),
    )

    with patch.object(
        scheduling,
        "check_availability_with_status",
        wraps=scheduling.check_availability_with_status,
    ) as check_availability_mock:
        second = service.handle_message(
            ChatMessageInput(
                message="Dr. Emily is fine",
                conversation_id=conversation_id,
            ),
        )

    check_availability_mock.assert_called_once()
    assert second.intent == ChatReceptionistIntent.AVAILABILITY_RESULTS
    assert "Dr. Emily Carter" in second.reply
    assert "which time works better" in second.reply.lower()
    chat_context = second.conversation.conversation_metadata["chat_context"]
    assert chat_context["selected_doctor_name"] == "Dr. Emily Carter"
    assert chat_context.get("offered_slots")


def test_offered_doctor_selection_does_not_expose_doctor_ids_in_reply() -> None:
    interpreter = StubChatTurnUnderstandingInterpreter(
        ChatTurnUnderstandingResult(
            intent=ChatTurnIntent.FALLBACK,
            confidence=0.2,
            reason="unused for first turn",
        ),
    )
    service = _create_runtime_service(interpreter)

    first = service.handle_message(ChatMessageInput(message="cardiology"))
    reed_id = _reed_doctor_id(service)

    interpreter.result = ChatTurnUnderstandingResult(
        intent=ChatTurnIntent.APPOINTMENT_REQUEST,
        confidence=0.9,
        reason="offered doctor selection",
        extracted_fields=ExtractedTurnFields(
            doctor_name_raw="Dr. Reed",
        ),
    )

    second = service.handle_message(
        ChatMessageInput(
            message="It can be Dr. Reed",
            conversation_id=first.conversation.id,
        ),
    )

    assert reed_id not in second.reply


def test_unknown_doctor_with_offered_doctors_returns_clarification() -> None:
    interpreter = StubChatTurnUnderstandingInterpreter(
        ChatTurnUnderstandingResult(
            intent=ChatTurnIntent.FALLBACK,
            confidence=0.2,
            reason="unused for first turn",
        ),
    )
    service = _create_runtime_service(interpreter)
    scheduling = service.scheduling

    first = service.handle_message(ChatMessageInput(message="cardiology"))

    interpreter.result = ChatTurnUnderstandingResult(
        intent=ChatTurnIntent.APPOINTMENT_REQUEST,
        confidence=0.9,
        reason="unknown doctor among offered options",
        extracted_fields=ExtractedTurnFields(
            doctor_name_raw="Dr. Unknown",
        ),
    )

    with patch.object(
        scheduling,
        "check_availability",
        wraps=scheduling.check_availability,
    ) as check_availability_mock:
        second = service.handle_message(
            ChatMessageInput(
                message="I'd prefer Dr. Unknown",
                conversation_id=first.conversation.id,
            ),
        )

    assert second.intent == ChatReceptionistIntent.APPOINTMENT_REQUEST
    assert "doctor" in second.reply.lower()
    assert "options i shared" in second.reply.lower()
    check_availability_mock.assert_not_called()
    chat_context = second.conversation.conversation_metadata["chat_context"]
    assert chat_context.get("selected_doctor_name") != "Dr. Unknown"


def test_slot_selection_with_offered_slots_not_intercepted_by_intake() -> None:
    interpreter = StubChatTurnUnderstandingInterpreter(
        ChatTurnUnderstandingResult(
            intent=ChatTurnIntent.FALLBACK,
            confidence=0.2,
            reason="noop for availability turn",
        ),
    )
    service = _create_runtime_service(interpreter)
    scheduling = service.scheduling

    availability_result = service.handle_message(
        ChatMessageInput(message="Dr. Emily Carter on 2026-07-02"),
    )
    conversation_id = availability_result.conversation.id
    offered_slots = availability_result.conversation.conversation_metadata["chat_context"][
        "offered_slots"
    ]
    assert offered_slots

    interpreter.result = ChatTurnUnderstandingResult(
        intent=ChatTurnIntent.SLOT_SELECTION,
        confidence=0.95,
        reason="slot selection",
        selected_slot_reference=str(offered_slots[0]["availability_slot_id"]),
    )

    with patch.object(
        scheduling,
        "check_availability",
        wraps=scheduling.check_availability,
    ) as check_availability_mock:
        hold_result = service.handle_message(
            ChatMessageInput(
                message="09:00",
                conversation_id=conversation_id,
            ),
        )

    check_availability_mock.assert_not_called()
    assert hold_result.intent == ChatReceptionistIntent.HOLD_CREATED
    assert interpreter.calls
    assert hold_result.conversation.conversation_metadata["chat_context"].get("hold_id")


def _scheduling_with_reed_july_slots() -> object:
    from datetime import UTC, datetime

    from app.domain.scheduling.enums import AvailabilitySlotStatus

    scheduling = create_demo_scheduling_service_with_emily_july_availability()
    reed = next(
        doctor
        for doctor in scheduling.list_doctors()
        if doctor.full_name == "Dr. Michael Reed"
    )
    reed_slots = [
        create_availability_slot(
            doctor_id=reed.id,
            start_time=datetime(2026, 7, 3, 10, 0, tzinfo=UTC),
            status=AvailabilitySlotStatus.AVAILABLE,
        ),
        create_availability_slot(
            doctor_id=reed.id,
            start_time=datetime(2026, 7, 3, 11, 0, tzinfo=UTC),
            status=AvailabilitySlotStatus.AVAILABLE,
        ),
    ]
    existing_slots = list(scheduling.availability_slots.slots)  # type: ignore[attr-defined]
    return create_service(
        specialties=list(scheduling.specialties.list_active()),
        doctors=list(scheduling.doctors.list_active()),
        availability_slots=[*existing_slots, *reed_slots],
    )


def _create_runtime_service_with_scheduling(
    interpreter: object | None,
    scheduling: object,
    *,
    clinic_time_service: object | None = None,
) -> ChatReceptionistService:
    from app.services.conversations import ConversationService

    repository = FakeConversationRepository()
    conversations = ConversationService(repository=repository)
    date_parser = NaturalLanguageDateParser(clock=FixedClock(current_date=date(2026, 7, 1)))
    return create_chat_receptionist_service(
        conversations=conversations,
        scheduling=scheduling,  # type: ignore[arg-type]
        hold_service=_create_hold_service(),
        date_parser=date_parser,
        time_preference_parser=TimePreferenceParser(),
        clinic_time_service=clinic_time_service,  # type: ignore[arg-type]
        chat_turn_understanding_interpreter=interpreter,  # type: ignore[arg-type]
    )


def test_soonest_cardiology_searches_from_clinic_today() -> None:
    interpreter = StubChatTurnUnderstandingInterpreter(
        ChatTurnUnderstandingResult(
            intent=ChatTurnIntent.APPOINTMENT_REQUEST,
            confidence=0.9,
            reason="soonest cardiology appointment request",
            extracted_fields=ExtractedTurnFields(
                specialty_raw="cardiology",
            ),
        ),
    )
    scheduling = _scheduling_with_reed_july_slots()
    service = _create_runtime_service_with_scheduling(
        interpreter,
        scheduling,
        clinic_time_service=make_test_clinic_time_service(),
    )

    with patch.object(
        service.scheduling,
        "check_availability_with_status",
        wraps=service.scheduling.check_availability_with_status,
    ) as doctor_availability_mock:
        result = service.handle_message(
            ChatMessageInput(message="soonest cardiology appointment"),
        )

    doctor_availability_mock.assert_called_once()
    call_kwargs = doctor_availability_mock.call_args.kwargs
    assert call_kwargs["start_from"].date().isoformat() == "2026-07-01"
    assert result.intent == ChatReceptionistIntent.AVAILABILITY_RESULTS
    assert "Dr. Michael Reed" in result.reply
    assert "which time works better" in result.reply.lower()
    chat_context = result.conversation.conversation_metadata["chat_context"]
    assert chat_context.get("soonest_requested") is True
    assert chat_context.get("offered_slots")


def test_dr_emily_soonest_searches_doctor_availability_from_clinic_today() -> None:
    interpreter = StubChatTurnUnderstandingInterpreter(
        ChatTurnUnderstandingResult(
            intent=ChatTurnIntent.APPOINTMENT_REQUEST,
            confidence=0.9,
            reason="soonest doctor request",
            extracted_fields=ExtractedTurnFields(
                doctor_name_raw="Dr. Emily",
            ),
        ),
    )
    service = _create_runtime_service(
        interpreter,
        clinic_time_service=make_test_clinic_time_service(),
    )
    scheduling = service.scheduling

    with patch.object(
        scheduling,
        "check_availability_with_status",
        wraps=scheduling.check_availability_with_status,
    ) as doctor_availability_mock:
        result = service.handle_message(
            ChatMessageInput(message="I want Dr. Emily soonest available"),
        )

    doctor_availability_mock.assert_called_once()
    call_kwargs = doctor_availability_mock.call_args.kwargs
    assert call_kwargs["start_from"].date().isoformat() == "2026-07-01"
    assert result.intent == ChatReceptionistIntent.AVAILABILITY_RESULTS
    assert "Dr. Emily Carter" in result.reply
    assert "which time works better" in result.reply.lower()
    chat_context = result.conversation.conversation_metadata["chat_context"]
    assert chat_context.get("soonest_requested") is True
    assert chat_context.get("offered_slots")


def test_earliest_search_with_no_availability_returns_natural_guidance() -> None:
    interpreter = StubChatTurnUnderstandingInterpreter(
        ChatTurnUnderstandingResult(
            intent=ChatTurnIntent.APPOINTMENT_REQUEST,
            confidence=0.9,
            reason="soonest cardiology appointment request",
            extracted_fields=ExtractedTurnFields(
                specialty_raw="cardiology",
            ),
        ),
    )
    service = _create_runtime_service(
        interpreter,
        clinic_time_service=make_test_clinic_time_service(),
    )

    result = service.handle_message(
        ChatMessageInput(message="soonest cardiology appointment"),
    )

    assert result.intent == ChatReceptionistIntent.AVAILABILITY_NO_SLOTS
    assert "not seeing openings" in result.reply.lower()
    assert "YYYY-MM-DD" not in result.reply
    assert result.conversation.conversation_metadata["chat_context"].get("offered_slots") == []


def test_dermatologist_listing_includes_follow_up_and_awaiting_flag() -> None:
    service = _create_runtime_service(None)

    result = service.handle_message(
        ChatMessageInput(message="I need a dermatologist"),
    )

    assert result.intent == ChatReceptionistIntent.SPECIALTY_DOCTORS
    assert "What day or time works best?" in result.reply
    assert "The following doctors are available" not in result.reply
    chat_context = result.conversation.conversation_metadata["chat_context"]
    assert chat_context["appointment_intake_awaiting"] == "date_or_time_preference"
    assert chat_context["selected_specialty_name"] == "Dermatology"


def test_what_about_wednesday_reenters_availability_without_generic_fallback() -> None:
    spy = SpyChatTurnUnderstandingInterpreter()
    service = _create_runtime_service(spy)
    scheduling = service.scheduling

    first = service.handle_message(
        ChatMessageInput(message="Dr. Emily Carter on 2026-07-02"),
    )
    conversation_id = first.conversation.id
    first_context = first.conversation.conversation_metadata["chat_context"]
    assert first_context["selected_doctor_name"] == "Dr. Emily Carter"
    assert first_context["requested_date"] == "2026-07-02"

    with (
        patch.object(
            service._appointment_intake,
            "_parse_bare_weekday",
            return_value="2026-07-08",
        ),
        patch.object(
            scheduling,
            "check_availability",
            wraps=scheduling.check_availability,
        ) as check_availability_mock,
    ):
        second = service.handle_message(
            ChatMessageInput(
                message="What about Wednesday?",
                conversation_id=conversation_id,
            ),
        )

    assert second.intent != ChatReceptionistIntent.FALLBACK
    assert "book, cancel, or reschedule" not in second.reply.lower()
    check_availability_mock.assert_called_once()
    chat_context = second.conversation.conversation_metadata["chat_context"]
    assert chat_context["requested_date"] == "2026-07-08"
    assert chat_context["selected_doctor_name"] == "Dr. Emily Carter"
    assert chat_context.get("offered_slots") == []


def test_what_about_wednesday_without_appointment_context_uses_generic_fallback() -> None:
    service = _create_runtime_service(None)

    result = service.handle_message(ChatMessageInput(message="What about Wednesday?"))

    assert result.intent == ChatReceptionistIntent.FALLBACK
    assert "book, cancel, or reschedule" in result.reply.lower()


def test_afternoon_follow_up_reenters_availability_with_existing_provider_and_date() -> None:
    spy = SpyChatTurnUnderstandingInterpreter()
    service = _create_runtime_service(spy)
    scheduling = service.scheduling

    first = service.handle_message(
        ChatMessageInput(message="Dr. Emily Carter on 2026-07-02"),
    )
    conversation_id = first.conversation.id

    with patch.object(
        scheduling,
        "check_availability",
        wraps=scheduling.check_availability,
    ) as check_availability_mock:
        second = service.handle_message(
            ChatMessageInput(
                message="afternoon",
                conversation_id=conversation_id,
            ),
        )

    assert second.intent != ChatReceptionistIntent.FALLBACK
    check_availability_mock.assert_called_once()
    chat_context = second.conversation.conversation_metadata["chat_context"]
    assert chat_context["requested_time_window"]["label"] == "afternoon"
    assert chat_context["selected_doctor_name"] == "Dr. Emily Carter"
    assert chat_context["requested_date"] == "2026-07-02"
