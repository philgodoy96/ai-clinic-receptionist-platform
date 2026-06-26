from __future__ import annotations

from datetime import date
from uuid import uuid4

from app.domain.chat_turn_understanding import (
    ChatTurnIntent,
    ChatTurnUnderstandingRequest,
    ChatTurnUnderstandingResult,
    ExtractedTurnFields,
    FieldIssue,
)
from app.models.scheduling import Doctor
from app.services.chat_appointment_intake import ChatAppointmentIntakeOrchestrator
from app.services.date_parsing import FixedClock, NaturalLanguageDateParser
from app.services.fake_chat_turn_understanding_interpreter import (
    FakeChatTurnUnderstandingInterpreter,
)
from app.services.scheduling import SchedulingService
from tests.clinic_time_test_support import make_test_clinic_time_service
from tests.test_scheduling_services import (
    create_demo_scheduling_service,
    create_service,
    create_specialty,
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
            confidence=0.9,
            reason="spy fallback",
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


def _create_orchestrator(
    *,
    interpreter: object | None = None,
    scheduling: SchedulingService | None = None,
    reference_date: date | None = None,
) -> ChatAppointmentIntakeOrchestrator:
    scheduling = scheduling or create_demo_scheduling_service()
    reference_date = reference_date or date(2026, 7, 1)
    return ChatAppointmentIntakeOrchestrator(
        scheduling=scheduling,
        date_parser=NaturalLanguageDateParser(clock=FixedClock(current_date=reference_date)),
        clinic_time_service=make_test_clinic_time_service(),
        chat_turn_understanding_interpreter=interpreter,  # type: ignore[arg-type]
    )


def _cardiology_specialty_id(scheduling: SchedulingService) -> str:
    for specialty in scheduling.list_specialties():
        if specialty.name == "Cardiology":
            return str(specialty.id)
    raise AssertionError("Cardiology specialty not found")


def _doctor_id_by_name(scheduling: SchedulingService, full_name: str) -> str:
    for doctor in scheduling.list_doctors():
        if doctor.full_name == full_name:
            return str(doctor.id)
    raise AssertionError(f"{full_name} not found")


def test_no_interpreter_returns_noop_without_calling_ctu() -> None:
    orchestrator = _create_orchestrator(interpreter=None)

    result = orchestrator.handle(message="cardiology next Monday", chat_context={})

    assert result.intent == "noop"
    assert result.chat_context_updates == {}


def test_ctu_fallback_returns_no_context_updates() -> None:
    interpreter = StubChatTurnUnderstandingInterpreter(
        ChatTurnUnderstandingResult(
            intent=ChatTurnIntent.FALLBACK,
            confidence=0.2,
            reason="low confidence fallback",
            clarification_question="Could you clarify?",
        ),
    )
    orchestrator = _create_orchestrator(interpreter=interpreter)

    result = orchestrator.handle(message="cardiology", chat_context={})

    assert result.intent == "noop"
    assert result.chat_context_updates == {}
    assert len(interpreter.calls) == 1


def test_unsupported_intent_returns_no_context_updates() -> None:
    interpreter = StubChatTurnUnderstandingInterpreter(
        ChatTurnUnderstandingResult(
            intent=ChatTurnIntent.GREETING,
            confidence=0.95,
            reason="user greeted",
        ),
    )
    orchestrator = _create_orchestrator(interpreter=interpreter)

    result = orchestrator.handle(message="hello", chat_context={})

    assert result.intent == "noop"
    assert result.chat_context_updates == {}


def test_specialty_extraction_validates_against_backend() -> None:
    scheduling = create_demo_scheduling_service()
    interpreter = StubChatTurnUnderstandingInterpreter(
        ChatTurnUnderstandingResult(
            intent=ChatTurnIntent.APPOINTMENT_REQUEST,
            confidence=0.9,
            reason="specialty request",
            extracted_fields=ExtractedTurnFields(
                specialty_raw="cardiology",
            ),
        ),
    )
    orchestrator = _create_orchestrator(interpreter=interpreter, scheduling=scheduling)

    result = orchestrator.handle(message="cardiology", chat_context={})

    assert result.intent == "appointment_intake"
    assert result.chat_context_updates["selected_specialty_name"] == "Cardiology"
    assert result.chat_context_updates["selected_specialty_id"] == _cardiology_specialty_id(
        scheduling,
    )


def test_unknown_specialty_returns_clarification_without_update() -> None:
    interpreter = StubChatTurnUnderstandingInterpreter(
        ChatTurnUnderstandingResult(
            intent=ChatTurnIntent.APPOINTMENT_REQUEST,
            confidence=0.9,
            reason="unknown specialty",
            extracted_fields=ExtractedTurnFields(
                specialty_raw="neurology",
            ),
        ),
    )
    orchestrator = _create_orchestrator(interpreter=interpreter)

    result = orchestrator.handle(message="neurology", chat_context={})

    assert result.intent == "clarification"
    assert "specialty" in (result.content or "").lower()
    assert result.chat_context_updates == {}


def test_doctor_partial_match_resolves_dr_reed() -> None:
    scheduling = create_demo_scheduling_service()
    interpreter = StubChatTurnUnderstandingInterpreter(
        ChatTurnUnderstandingResult(
            intent=ChatTurnIntent.APPOINTMENT_REQUEST,
            confidence=0.9,
            reason="doctor request",
            extracted_fields=ExtractedTurnFields(
                doctor_name_raw="Dr. Reed",
            ),
        ),
    )
    orchestrator = _create_orchestrator(interpreter=interpreter, scheduling=scheduling)

    result = orchestrator.handle(message="Dr. Reed", chat_context={})

    assert result.intent == "appointment_intake"
    assert result.chat_context_updates["selected_doctor_name"] == "Dr. Michael Reed"
    assert result.chat_context_updates["selected_doctor_id"] == _doctor_id_by_name(
        scheduling,
        "Dr. Michael Reed",
    )


def test_doctor_partial_match_resolves_dr_emily() -> None:
    scheduling = create_demo_scheduling_service()
    interpreter = StubChatTurnUnderstandingInterpreter(
        ChatTurnUnderstandingResult(
            intent=ChatTurnIntent.APPOINTMENT_REQUEST,
            confidence=0.9,
            reason="doctor request",
            extracted_fields=ExtractedTurnFields(
                doctor_name_raw="Dr. Emily",
            ),
        ),
    )
    orchestrator = _create_orchestrator(interpreter=interpreter, scheduling=scheduling)

    result = orchestrator.handle(message="Dr. Emily", chat_context={})

    assert result.intent == "appointment_intake"
    assert result.chat_context_updates["selected_doctor_name"] == "Dr. Emily Carter"
    assert result.chat_context_updates["selected_doctor_id"] == _doctor_id_by_name(
        scheduling,
        "Dr. Emily Carter",
    )


def test_unknown_doctor_returns_clarification_without_update() -> None:
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
    orchestrator = _create_orchestrator(interpreter=interpreter)

    result = orchestrator.handle(message="Dr. Unknown", chat_context={})

    assert result.intent == "clarification"
    assert result.chat_context_updates == {}


def test_ambiguous_doctor_match_returns_clarification_without_update() -> None:
    dermatology = create_specialty(name="Dermatology")
    doctors = [
        Doctor(
            id=uuid4(),
            specialty_id=dermatology.id,
            full_name="Dr. Emily Carter",
            email="emily.carter@example-clinic.test",
            phone_number="+1-555-0101",
            is_active=True,
        ),
        Doctor(
            id=uuid4(),
            specialty_id=dermatology.id,
            full_name="Dr. Emily Brooks",
            email="emily.brooks@example-clinic.test",
            phone_number="+1-555-0105",
            is_active=True,
        ),
    ]
    scheduling = create_service(specialties=[dermatology], doctors=doctors)
    interpreter = StubChatTurnUnderstandingInterpreter(
        ChatTurnUnderstandingResult(
            intent=ChatTurnIntent.APPOINTMENT_REQUEST,
            confidence=0.9,
            reason="ambiguous doctor",
            extracted_fields=ExtractedTurnFields(
                doctor_name_raw="Dr. Emily",
            ),
        ),
    )
    orchestrator = _create_orchestrator(interpreter=interpreter, scheduling=scheduling)

    result = orchestrator.handle(message="Dr. Emily", chat_context={})

    assert result.intent == "clarification"
    assert "which doctor" in (result.content or "").lower()
    assert result.chat_context_updates == {}


def test_doctor_specialty_mismatch_returns_clarification() -> None:
    scheduling = create_demo_scheduling_service()
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
    orchestrator = _create_orchestrator(interpreter=interpreter, scheduling=scheduling)

    result = orchestrator.handle(
        message="cardiology with Dr. Emily Carter",
        chat_context={},
    )

    assert result.intent == "clarification"
    assert "not in cardiology" in (result.content or "").lower()
    assert result.chat_context_updates == {}


def test_next_monday_resolves_to_requested_date() -> None:
    interpreter = StubChatTurnUnderstandingInterpreter(
        ChatTurnUnderstandingResult(
            intent=ChatTurnIntent.APPOINTMENT_REQUEST,
            confidence=0.9,
            reason="date request",
            extracted_fields=ExtractedTurnFields(
                appointment_date_raw="next Monday",
            ),
        ),
    )
    orchestrator = _create_orchestrator(
        interpreter=interpreter,
        reference_date=date(2026, 7, 1),
    )

    result = orchestrator.handle(message="next Monday", chat_context={})

    assert result.intent == "appointment_intake"
    assert result.chat_context_updates["requested_date"] == "2026-07-06"


def test_afternoon_resolves_to_requested_time_window() -> None:
    interpreter = StubChatTurnUnderstandingInterpreter(
        ChatTurnUnderstandingResult(
            intent=ChatTurnIntent.APPOINTMENT_REQUEST,
            confidence=0.9,
            reason="time window request",
            extracted_fields=ExtractedTurnFields(
                appointment_time_window_raw="afternoon",
                appointment_time_window="afternoon",
            ),
        ),
    )
    orchestrator = _create_orchestrator(interpreter=interpreter)

    result = orchestrator.handle(message="afternoon", chat_context={})

    assert result.intent == "appointment_intake"
    assert result.chat_context_updates["requested_time_window"] == {
        "label": "afternoon",
        "start_time": "12:00",
        "end_time": "17:00",
    }


def test_soonest_cardiology_marks_soonest_requested() -> None:
    scheduling = create_demo_scheduling_service()
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
    orchestrator = _create_orchestrator(interpreter=interpreter, scheduling=scheduling)

    result = orchestrator.handle(message="soonest cardiology appointment", chat_context={})

    assert result.intent == "appointment_intake"
    assert result.search_criteria is not None
    assert result.search_criteria.soonest_requested is True
    assert result.search_criteria.search_start_date == "2026-07-01"
    assert result.search_criteria.search_end_date == "2026-07-15"
    assert result.chat_context_updates["selected_specialty_name"] == "Cardiology"


def test_specialty_without_date_sets_earliest_search_window() -> None:
    scheduling = create_demo_scheduling_service()
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
    orchestrator = _create_orchestrator(interpreter=interpreter, scheduling=scheduling)

    result = orchestrator.handle(
        message="I'd like to schedule with a dermatologist",
        chat_context={},
    )

    assert result.intent == "appointment_intake"
    assert result.search_criteria is not None
    assert result.search_criteria.soonest_requested is False
    assert result.search_criteria.search_start_date == "2026-07-01"
    assert result.search_criteria.search_end_date == "2026-07-15"
    assert result.search_criteria.requested_date is None


def test_existing_conflicting_doctor_is_not_overwritten_silently() -> None:
    scheduling = create_demo_scheduling_service()
    emily_id = _doctor_id_by_name(scheduling, "Dr. Emily Carter")
    interpreter = StubChatTurnUnderstandingInterpreter(
        ChatTurnUnderstandingResult(
            intent=ChatTurnIntent.APPOINTMENT_REQUEST,
            confidence=0.9,
            reason="doctor change request",
            extracted_fields=ExtractedTurnFields(
                doctor_name_raw="Dr. Reed",
            ),
        ),
    )
    orchestrator = _create_orchestrator(interpreter=interpreter, scheduling=scheduling)

    result = orchestrator.handle(
        message="Dr. Reed",
        chat_context={
            "selected_doctor_id": emily_id,
            "selected_doctor_name": "Dr. Emily Carter",
        },
    )

    assert result.intent == "clarification"
    assert "previously selected" in (result.content or "").lower()
    assert result.chat_context_updates == {}


def test_known_and_offered_doctors_are_included_in_ctu_request() -> None:
    scheduling = create_demo_scheduling_service()
    spy = SpyChatTurnUnderstandingInterpreter()
    orchestrator = _create_orchestrator(interpreter=spy, scheduling=scheduling)
    offered_doctor_id = str(uuid4())

    orchestrator.handle(
        message="cardiology",
        chat_context={
            "offered_doctors": [
                {
                    "doctor_id": offered_doctor_id,
                    "doctor_name": "Dr. Offered Smith",
                },
            ],
        },
    )

    assert len(spy.calls) == 1
    request = spy.calls[0]
    known_names = {doctor.full_name for doctor in request.known_doctors}
    assert "Dr. Emily Carter" in known_names
    assert "Dr. Michael Reed" in known_names
    assert "Dr. Offered Smith" in known_names


def test_ctu_ambiguous_fields_return_clarification_without_update() -> None:
    interpreter = StubChatTurnUnderstandingInterpreter(
        ChatTurnUnderstandingResult(
            intent=ChatTurnIntent.APPOINTMENT_REQUEST,
            confidence=0.9,
            reason="ambiguous doctor from ctu",
            ambiguous_fields=[
                FieldIssue(
                    field="doctor_name",
                    source_text="Dr. Emily",
                    reason="multiple_doctor_matches",
                    clarification_question="Which doctor did you mean?",
                ),
            ],
        ),
    )
    orchestrator = _create_orchestrator(interpreter=interpreter)

    result = orchestrator.handle(message="Dr. Emily", chat_context={})

    assert result.intent == "clarification"
    assert result.content == "Which doctor did you mean?"
    assert result.chat_context_updates == {}


def test_fake_interpreter_cardiology_message_updates_specialty() -> None:
    scheduling = create_demo_scheduling_service()
    orchestrator = _create_orchestrator(
        interpreter=FakeChatTurnUnderstandingInterpreter(),
        scheduling=scheduling,
    )

    result = orchestrator.handle(message="I need cardiology", chat_context={})

    assert result.intent == "appointment_intake"
    assert result.chat_context_updates["selected_specialty_name"] == "Cardiology"
