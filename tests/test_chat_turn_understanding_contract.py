from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.domain.chat_turn_understanding import (
    ChatTurnIntent,
    ChatTurnUnderstandingRequest,
    ChatTurnUnderstandingResult,
    ConfirmationDecision,
    ConversationState,
    ExpectedResponseType,
    ExtractedTurnFields,
    FieldIssue,
    KnownDoctor,
    KnownSpecialty,
    OfferedSlot,
    PatientStatusAnswer,
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


@pytest.mark.parametrize(
    ("enum_type", "member", "expected_value"),
    [
        (ConversationState, ConversationState.IDLE, "idle"),
        (
            ConversationState,
            ConversationState.COLLECTING_APPOINTMENT_REQUEST,
            "collecting_appointment_request",
        ),
        (ExpectedResponseType, ExpectedResponseType.SLOT_SELECTION, "slot_selection"),
        (ChatTurnIntent, ChatTurnIntent.APPOINTMENT_REQUEST, "appointment_request"),
        (ConfirmationDecision, ConfirmationDecision.WANTS_CHANGE, "wants_change"),
        (PatientStatusAnswer, PatientStatusAnswer.EXISTING_PATIENT, "existing_patient"),
    ],
)
def test_enum_values_serialize_as_expected(
    enum_type: type,
    member: object,
    expected_value: str,
) -> None:
    assert member.value == expected_value  # type: ignore[attr-defined]
    assert enum_type(expected_value) is member


def test_request_defaults() -> None:
    request = ChatTurnUnderstandingRequest(
        conversation_state=ConversationState.IDLE,
        expected_response_type=ExpectedResponseType.OPEN_TEXT,
        latest_user_message="Hello",
        allowed_intents=[ChatTurnIntent.GREETING],
    )

    assert request.last_assistant_question is None
    assert request.current_context == {}
    assert request.offered_slots == []
    assert request.known_specialties == []
    assert request.known_doctors == []
    assert request.locale is None


def test_response_defaults() -> None:
    understanding = ChatTurnUnderstandingResult(
        intent=ChatTurnIntent.GREETING,
        confidence=0.8,
        reason="User greeted the assistant.",
    )

    assert understanding.confirmation_decision is ConfirmationDecision.NOT_APPLICABLE
    assert understanding.patient_status_answer is PatientStatusAnswer.NOT_APPLICABLE
    assert understanding.extracted_fields == ExtractedTurnFields()
    assert understanding.missing_fields == []
    assert understanding.ambiguous_fields == []
    assert understanding.selected_slot_reference is None
    assert understanding.clarification_question is None


@pytest.mark.parametrize("confidence", [-0.01, 1.01])
def test_confidence_rejects_out_of_range_values(confidence: float) -> None:
    with pytest.raises(ValidationError):
        ChatTurnUnderstandingResult(
            intent=ChatTurnIntent.FALLBACK,
            confidence=confidence,
            reason="Out of range confidence should fail validation.",
        )


def test_mutable_defaults_are_not_shared() -> None:
    request_a = ChatTurnUnderstandingRequest(
        conversation_state=ConversationState.OFFERING_SLOTS,
        expected_response_type=ExpectedResponseType.SLOT_SELECTION,
        latest_user_message="The first one",
        allowed_intents=[ChatTurnIntent.SLOT_SELECTION],
    )
    request_b = ChatTurnUnderstandingRequest(
        conversation_state=ConversationState.OFFERING_SLOTS,
        expected_response_type=ExpectedResponseType.SLOT_SELECTION,
        latest_user_message="The second one",
        allowed_intents=[ChatTurnIntent.SLOT_SELECTION],
    )
    understanding_a = ChatTurnUnderstandingResult(
        intent=ChatTurnIntent.SLOT_SELECTION,
        confidence=0.9,
        reason="Selected a slot.",
    )
    understanding_b = ChatTurnUnderstandingResult(
        intent=ChatTurnIntent.SLOT_SELECTION,
        confidence=0.9,
        reason="Selected another slot.",
    )

    request_a.current_context["specialty"] = "Dermatology"
    request_a.offered_slots.append(
        OfferedSlot(reference="slot-1", start_time="2026-07-02T10:30:00"),
    )
    request_a.known_specialties.append(KnownSpecialty(id="spec-1", name="Dermatology"))
    request_a.known_doctors.append(KnownDoctor(id="doc-1", full_name="Dr. Emily Carter"))
    understanding_a.missing_fields.append(
        FieldIssue(field="email", reason="required_for_new_patient"),
    )
    understanding_a.ambiguous_fields.append(
        FieldIssue(field="doctor_name", reason="multiple_matches"),
    )
    understanding_a.extracted_fields.patient_name = "Jane Doe"

    assert request_b.current_context == {}
    assert request_b.offered_slots == []
    assert request_b.known_specialties == []
    assert request_b.known_doctors == []
    assert understanding_b.missing_fields == []
    assert understanding_b.ambiguous_fields == []
    assert understanding_b.extracted_fields.patient_name is None


def test_extracted_fields_support_raw_and_normalized_values() -> None:
    fields = ExtractedTurnFields(
        date_of_birth="1990-01-15",
        date_of_birth_raw="January 15, 1990",
        specialty="Dermatology",
        specialty_raw="skin doctor",
        doctor_name="Dr. Emily Carter",
        doctor_name_raw="Emily",
        appointment_date="2026-07-02",
        appointment_date_raw="next Tuesday",
        appointment_time="10:30",
        appointment_time_raw="ten thirty",
        appointment_time_window="morning",
        appointment_time_window_raw="in the morning",
    )

    payload = fields.model_dump()

    assert payload["date_of_birth"] == "1990-01-15"
    assert payload["date_of_birth_raw"] == "January 15, 1990"
    assert payload["specialty"] == "Dermatology"
    assert payload["specialty_raw"] == "skin doctor"
    assert payload["appointment_time_window"] == "morning"
    assert payload["appointment_time_window_raw"] == "in the morning"


def test_offered_slot_and_known_catalog_models_serialize_correctly() -> None:
    offered_slot = OfferedSlot(
        reference="slot-42",
        start_time="2026-07-02T10:30:00",
        doctor_id="doc-1",
        doctor_name="Dr. Emily Carter",
        specialty_id="spec-1",
        specialty_name="Dermatology",
        display_label="Tue Jul 2 at 10:30 AM with Dr. Emily Carter",
    )
    known_specialty = KnownSpecialty(
        id="spec-1",
        name="Dermatology",
        aliases=["skin", "derm"],
    )
    known_doctor = KnownDoctor(
        id="doc-1",
        full_name="Dr. Emily Carter",
        specialty_id="spec-1",
        aliases=["Dr. Carter", "Emily Carter"],
    )

    assert offered_slot.model_dump() == {
        "reference": "slot-42",
        "start_time": "2026-07-02T10:30:00",
        "doctor_id": "doc-1",
        "doctor_name": "Dr. Emily Carter",
        "specialty_id": "spec-1",
        "specialty_name": "Dermatology",
        "display_label": "Tue Jul 2 at 10:30 AM with Dr. Emily Carter",
    }
    assert known_specialty.model_dump() == {
        "id": "spec-1",
        "name": "Dermatology",
        "aliases": ["skin", "derm"],
    }
    assert known_doctor.model_dump() == {
        "id": "doc-1",
        "full_name": "Dr. Emily Carter",
        "specialty_id": "spec-1",
        "aliases": ["Dr. Carter", "Emily Carter"],
    }


def test_field_issue_supports_ambiguity_candidates_and_clarification_question() -> None:
    issue = FieldIssue(
        field="doctor_name",
        source_text="Dr. Smith",
        reason="multiple_matches",
        candidates=[
            {"id": "doc-1", "full_name": "Dr. John Smith"},
            {"id": "doc-2", "full_name": "Dr. Jane Smith"},
        ],
        clarification_question="Did you mean Dr. John Smith or Dr. Jane Smith?",
    )

    payload = issue.model_dump()

    assert payload["field"] == "doctor_name"
    assert payload["source_text"] == "Dr. Smith"
    assert len(payload["candidates"]) == 2
    assert payload["clarification_question"].startswith("Did you mean")


@pytest.mark.parametrize("model_type", [ChatTurnUnderstandingRequest, ChatTurnUnderstandingResult])
def test_schema_does_not_expose_side_effect_action_fields(model_type: type[object]) -> None:
    field_names = set(model_type.model_fields.keys())  # type: ignore[attr-defined]

    assert SIDE_EFFECT_FIELD_NAMES.isdisjoint(field_names)
