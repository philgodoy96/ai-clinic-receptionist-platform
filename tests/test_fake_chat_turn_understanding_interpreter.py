from __future__ import annotations

from app.domain.chat_turn_understanding import (
    ChatTurnIntent,
    ChatTurnUnderstandingRequest,
    ChatTurnUnderstandingResult,
    ConfirmationDecision,
    ConversationState,
    ExpectedResponseType,
    KnownDoctor,
    KnownSpecialty,
    OfferedSlot,
    PatientStatusAnswer,
)
from app.services.fake_chat_turn_understanding_interpreter import (
    FakeChatTurnUnderstandingInterpreter,
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

ALL_INTENTS = list(ChatTurnIntent)
DERMATOLOGY = KnownSpecialty(id="spec-1", name="Dermatology", aliases=["derm"])


def _make_request(
    message: str,
    *,
    expected_response_type: ExpectedResponseType = ExpectedResponseType.OPEN_TEXT,
    conversation_state: ConversationState = ConversationState.IDLE,
    allowed_intents: list[ChatTurnIntent] | None = None,
    offered_slots: list[OfferedSlot] | None = None,
    known_specialties: list[KnownSpecialty] | None = None,
    known_doctors: list[KnownDoctor] | None = None,
) -> ChatTurnUnderstandingRequest:
    return ChatTurnUnderstandingRequest(
        conversation_state=conversation_state,
        expected_response_type=expected_response_type,
        latest_user_message=message,
        allowed_intents=allowed_intents or ALL_INTENTS,
        offered_slots=offered_slots or [],
        known_specialties=known_specialties or [],
        known_doctors=known_doctors or [],
    )


def _interpret(message: str, **kwargs: object) -> ChatTurnUnderstandingResult:
    interpreter = FakeChatTurnUnderstandingInterpreter()
    return interpreter.interpret(_make_request(message, **kwargs))  # type: ignore[arg-type]


def test_extracts_patient_name_and_natural_dob() -> None:
    result = _interpret(
        "Felipe Marques, Sep 19th 1996",
        expected_response_type=ExpectedResponseType.PATIENT_IDENTITY,
        conversation_state=ConversationState.COLLECTING_PATIENT_IDENTITY,
    )

    assert result.intent is ChatTurnIntent.PATIENT_IDENTITY_PROVIDED
    assert result.extracted_fields.patient_name == "Felipe Marques"
    assert result.extracted_fields.date_of_birth_raw == "Sep 19th 1996"
    assert result.extracted_fields.date_of_birth == "1996-09-19"
    assert result.confidence >= 0.85
    assert result.ambiguous_fields == []


def test_extracts_patient_name_and_iso_dob() -> None:
    result = _interpret("My name is Felipe Marques and my DOB is 1996-09-19")

    assert result.intent is ChatTurnIntent.PATIENT_IDENTITY_PROVIDED
    assert result.extracted_fields.patient_name == "Felipe Marques"
    assert result.extracted_fields.date_of_birth == "1996-09-19"


def test_accepts_day_first_impossible_numeric_dob() -> None:
    result = _interpret(
        "Felipe Marques, 19/09/1996",
        expected_response_type=ExpectedResponseType.PATIENT_IDENTITY,
    )

    assert result.extracted_fields.date_of_birth == "1996-09-19"
    assert result.extracted_fields.date_of_birth_raw == "19/09/1996"
    assert result.ambiguous_fields == []


def test_flags_ambiguous_numeric_dob() -> None:
    result = _interpret(
        "Felipe Marques, 09/10/1996",
        expected_response_type=ExpectedResponseType.PATIENT_IDENTITY,
    )

    assert result.extracted_fields.date_of_birth is None
    assert result.extracted_fields.date_of_birth_raw == "09/10/1996"
    assert len(result.ambiguous_fields) == 1
    issue = result.ambiguous_fields[0]
    assert issue.field == "date_of_birth"
    assert issue.reason == "ambiguous_numeric_date_format"
    assert issue.candidates == ["1996-09-10", "1996-10-09"]
    assert issue.clarification_question is not None


def test_extracts_new_patient_answer_identity_and_email() -> None:
    result = _interpret(
        "No, I'm new. Felipe Marques, born Sep 19th 1996, email felipe@example.com",
        expected_response_type=ExpectedResponseType.PATIENT_STATUS,
        conversation_state=ConversationState.COLLECTING_PATIENT_STATUS,
    )

    # Identity details are present, so patient_identity_provided is the primary intent.
    assert result.intent is ChatTurnIntent.PATIENT_IDENTITY_PROVIDED
    assert result.patient_status_answer is PatientStatusAnswer.NEW_PATIENT
    assert result.extracted_fields.patient_name == "Felipe Marques"
    assert result.extracted_fields.date_of_birth == "1996-09-19"
    assert result.extracted_fields.email == "felipe@example.com"


def test_maps_yes_to_existing_patient_for_patient_status() -> None:
    result = _interpret(
        "Yes",
        expected_response_type=ExpectedResponseType.PATIENT_STATUS,
        conversation_state=ConversationState.COLLECTING_PATIENT_STATUS,
    )

    assert result.intent is ChatTurnIntent.PATIENT_STATUS_ANSWER
    assert result.patient_status_answer is PatientStatusAnswer.EXISTING_PATIENT
    assert result.confirmation_decision is ConfirmationDecision.NOT_APPLICABLE


def test_maps_yes_to_booking_confirmation() -> None:
    result = _interpret(
        "Yes",
        expected_response_type=ExpectedResponseType.BOOKING_CONFIRMATION,
        conversation_state=ConversationState.CONFIRMING_BOOKING,
    )

    assert result.intent is ChatTurnIntent.CONFIRMATION
    assert result.confirmation_decision is ConfirmationDecision.CONFIRMED


def test_maps_thats_right_to_confirmed() -> None:
    result = _interpret(
        "That's right",
        expected_response_type=ExpectedResponseType.BOOKING_CONFIRMATION,
    )

    assert result.intent is ChatTurnIntent.CONFIRMATION
    assert result.confirmation_decision is ConfirmationDecision.CONFIRMED


def test_maps_change_the_time_to_wants_change() -> None:
    result = _interpret("Change the time")

    assert result.intent is ChatTurnIntent.CHANGE_REQUEST
    assert result.confirmation_decision is ConfirmationDecision.WANTS_CHANGE


def test_selects_first_offered_slot() -> None:
    slots = [
        OfferedSlot(reference="slot-1", start_time="2026-07-02T10:30:00"),
        OfferedSlot(reference="slot-2", start_time="2026-07-02T14:00:00"),
    ]
    result = _interpret(
        "the first one",
        expected_response_type=ExpectedResponseType.SLOT_SELECTION,
        conversation_state=ConversationState.OFFERING_SLOTS,
        offered_slots=slots,
    )

    assert result.intent is ChatTurnIntent.SLOT_SELECTION
    assert result.selected_slot_reference == "slot-1"


def test_selects_unique_2pm_offered_slot() -> None:
    slots = [
        OfferedSlot(reference="slot-morning", start_time="2026-07-02T10:30:00"),
        OfferedSlot(reference="slot-2pm", start_time="2026-07-02T14:00:00"),
    ]
    result = _interpret(
        "2pm",
        expected_response_type=ExpectedResponseType.SLOT_SELECTION,
        offered_slots=slots,
    )

    assert result.intent is ChatTurnIntent.SLOT_SELECTION
    assert result.selected_slot_reference == "slot-2pm"
    assert result.extracted_fields.appointment_time_raw == "2pm"
    assert result.extracted_fields.appointment_time == "14:00"


def test_flags_ambiguous_2pm_slot_match() -> None:
    slots = [
        OfferedSlot(reference="slot-a", start_time="2026-07-02T14:00:00"),
        OfferedSlot(reference="slot-b", start_time="2026-07-03T14:00:00"),
    ]
    result = _interpret("2pm", offered_slots=slots)

    assert result.selected_slot_reference is None
    assert len(result.ambiguous_fields) == 1
    assert result.ambiguous_fields[0].field == "selected_slot_reference"
    assert result.clarification_question is not None


def test_extracts_2pm_time_without_offered_slots() -> None:
    result = _interpret("2pm")

    assert result.extracted_fields.appointment_time_raw == "2pm"
    assert result.extracted_fields.appointment_time == "14:00"
    assert result.selected_slot_reference is None


def test_extracts_dermatology_next_thursday_afternoon() -> None:
    result = _interpret(
        "I want dermatology next Thursday afternoon",
        conversation_state=ConversationState.COLLECTING_APPOINTMENT_REQUEST,
        known_specialties=[DERMATOLOGY],
    )

    assert result.intent in {
        ChatTurnIntent.APPOINTMENT_REQUEST,
        ChatTurnIntent.AVAILABILITY_REQUEST,
    }
    assert result.extracted_fields.specialty_raw == "dermatology"
    assert result.extracted_fields.specialty == "Dermatology"
    assert result.extracted_fields.appointment_date_raw == "next Thursday"
    assert result.extracted_fields.appointment_time_window_raw == "afternoon"
    assert result.extracted_fields.appointment_time_window == "afternoon"
    assert result.extracted_fields.appointment_date is None


def test_resolves_specialty_alias_derm() -> None:
    result = _interpret("I need derm", known_specialties=[DERMATOLOGY])

    assert result.extracted_fields.specialty_raw == "derm"
    assert result.extracted_fields.specialty == "Dermatology"


def test_resolves_unique_doctor_partial_match() -> None:
    doctors = [
        KnownDoctor(id="doc-1", full_name="Dr. Emily Carter", specialty_id="spec-1"),
    ]
    result = _interpret("I'd like to see Emily", known_doctors=doctors)

    assert result.extracted_fields.doctor_name == "Dr. Emily Carter"
    assert result.extracted_fields.doctor_name_raw == "emily"
    assert result.ambiguous_fields == []


def test_flags_ambiguous_doctor_partial_match() -> None:
    doctors = [
        KnownDoctor(id="doc-1", full_name="Dr. John Smith"),
        KnownDoctor(id="doc-2", full_name="Dr. Jane Smith"),
    ]
    result = _interpret("Book with Smith", known_doctors=doctors)

    assert result.extracted_fields.doctor_name is None
    assert len(result.ambiguous_fields) == 1
    assert result.ambiguous_fields[0].field == "doctor_name"
    assert len(result.ambiguous_fields[0].candidates) == 2


def test_returns_fallback_for_unsupported_input() -> None:
    result = _interpret("xyzzy completely unsupported phrase")

    assert result.intent is ChatTurnIntent.FALLBACK
    assert result.confidence < 0.5
    assert result.clarification_question is not None
    assert "unsupported" in result.reason.lower() or "matched" in result.reason.lower()


def test_respects_allowed_intents() -> None:
    result = _interpret(
        "I want dermatology next Thursday afternoon",
        allowed_intents=[ChatTurnIntent.GREETING, ChatTurnIntent.FALLBACK],
        known_specialties=[DERMATOLOGY],
    )

    assert result.intent is ChatTurnIntent.FALLBACK
    assert "not allowed" in result.reason


def test_does_not_expose_side_effect_action_fields() -> None:
    result = _interpret(
        "Felipe Marques, Sep 19th 1996",
        expected_response_type=ExpectedResponseType.PATIENT_IDENTITY,
    )

    field_names = set(ChatTurnUnderstandingResult.model_fields.keys())
    assert SIDE_EFFECT_FIELD_NAMES.isdisjoint(field_names)

    dumped = result.model_dump()
    assert SIDE_EFFECT_FIELD_NAMES.isdisjoint(dumped.keys())
