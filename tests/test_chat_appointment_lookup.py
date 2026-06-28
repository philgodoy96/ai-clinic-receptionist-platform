from __future__ import annotations

from datetime import UTC, datetime

import pytest

from app.domain.chat_turn_understanding import (
    ChatTurnIntent,
    ChatTurnUnderstandingRequest,
    ConversationState,
    ExpectedResponseType,
)
from app.domain.scheduling.enums import AppointmentStatus
from app.services.chat_appointment_lookup import (
    APPOINTMENT_MANAGEMENT_AWAITING_COMPLETED,
    APPOINTMENT_MANAGEMENT_AWAITING_PATIENT_IDENTITY,
    APPOINTMENT_MANAGEMENT_MODE_LOOKUP,
)
from app.services.chat_receptionist import (
    ChatMessageInput,
    ChatReceptionistIntent,
    _is_appointment_lookup_request,
    _resolve_contextual_fallback_reply,
)
from app.services.fake_chat_turn_understanding_interpreter import (
    FakeChatTurnUnderstandingInterpreter,
)
from tests.test_chat_appointment_cancellation import (
    _add_appointments,
    _create_chat_service_with_patient,
    _friday_appointment,
    _wednesday_appointment,
    chat_context_id_not_exposed,
)
from tests.test_scheduling_services import create_appointment


def _lookup_message() -> str:
    return "Sure, I'd like to see what my scheduled appointments are"


@pytest.mark.parametrize(
    "message",
    [
        "show my scheduled appointments",
        "what appointments do I have?",
        "what are my upcoming appointments?",
        "can I see my appointments?",
        _lookup_message(),
        "do I have any appointments scheduled?",
    ],
)
def test_appointment_lookup_phrases_are_detected(message: str) -> None:
    assert _is_appointment_lookup_request(message.lower()) is True


@pytest.mark.parametrize(
    "message",
    [
        "I want to cancel my appointment",
        "I need to reschedule",
        "book an appointment",
    ],
)
def test_appointment_lookup_detection_excludes_cancel_reschedule_book(
    message: str,
) -> None:
    assert _is_appointment_lookup_request(message.lower()) is False


def test_lookup_without_resolved_patient_asks_for_identity() -> None:
    service, _repository, _patient, _emily, _reed = _create_chat_service_with_patient()

    result = service.handle_message(ChatMessageInput(message=_lookup_message()))

    assert result.intent == ChatReceptionistIntent.LIST_APPOINTMENTS
    reply = result.reply.lower()
    assert "full name" in reply
    assert "date of birth" in reply
    assert "book, cancel, or reschedule" not in reply
    chat_context = result.conversation.conversation_metadata["chat_context"]
    assert chat_context["appointment_management_mode"] == APPOINTMENT_MANAGEMENT_MODE_LOOKUP
    assert (
        chat_context["appointment_management_awaiting"]
        == APPOINTMENT_MANAGEMENT_AWAITING_PATIENT_IDENTITY
    )


def test_lookup_with_resolved_patient_lists_without_identity_prompt() -> None:
    service, _repository, patient, emily, _reed = _create_chat_service_with_patient()
    _add_appointments(
        service,
        [
            _wednesday_appointment(
                patient_id=patient.id,
                doctor_id=emily.id,
                specialty_id=emily.specialty_id,
            ),
        ],
    )
    started = service.handle_message(ChatMessageInput(message="hello"))
    service.conversations.merge_chat_context(
        conversation_id=started.conversation.id,
        chat_context={
            "resolved_patient_id": str(patient.id),
            "resolved_patient_name": patient.full_name,
            "resolved_patient_date_of_birth": patient.date_of_birth.isoformat(),
        },
    )

    result = service.handle_message(
        ChatMessageInput(
            message="show my scheduled appointments",
            conversation_id=started.conversation.id,
        ),
    )

    assert result.intent == ChatReceptionistIntent.LIST_APPOINTMENTS
    assert "full name" not in result.reply.lower()
    assert "Here are your upcoming scheduled appointments" in result.reply
    assert "Dermatology with Dr. Emily Carter" in result.reply
    assert "Wednesday, July 8" in result.reply
    assert "10:00" in result.reply
    assert "cancel or reschedule" in result.reply.lower()
    chat_context = result.conversation.conversation_metadata["chat_context"]
    assert chat_context.get("resolved_patient_id") == str(patient.id)
    assert chat_context_id_not_exposed(result.reply, chat_context=chat_context)


def test_lookup_name_only_asks_for_dob() -> None:
    service, _repository, _patient, _emily, _reed = _create_chat_service_with_patient()

    started = service.handle_message(ChatMessageInput(message=_lookup_message()))
    result = service.handle_message(
        ChatMessageInput(message="Felipe Godoy", conversation_id=started.conversation.id),
    )

    reply = result.reply.lower()
    assert "date of birth" in reply
    assert "full name and date of birth" not in reply
    chat_context = result.conversation.conversation_metadata["chat_context"]
    assert chat_context["appointment_management_identity"]["full_name"] == "Felipe Godoy"


def test_lookup_dob_only_asks_for_name() -> None:
    service, _repository, _patient, _emily, _reed = _create_chat_service_with_patient()

    started = service.handle_message(ChatMessageInput(message=_lookup_message()))
    result = service.handle_message(
        ChatMessageInput(message="1996-09-19", conversation_id=started.conversation.id),
    )

    reply = result.reply.lower()
    assert "full name" in reply
    assert "full name and date of birth" not in reply
    chat_context = result.conversation.conversation_metadata["chat_context"]
    assert chat_context["appointment_management_identity"]["date_of_birth"] == "1996-09-19"


def test_lookup_after_identity_resolution_lists_appointments() -> None:
    service, _repository, patient, emily, reed = _create_chat_service_with_patient()
    _add_appointments(
        service,
        [
            _wednesday_appointment(
                patient_id=patient.id,
                doctor_id=emily.id,
                specialty_id=emily.specialty_id,
            ),
            _friday_appointment(
                patient_id=patient.id,
                doctor_id=reed.id,
                specialty_id=reed.specialty_id,
            ),
        ],
    )

    started = service.handle_message(ChatMessageInput(message="what appointments do I have?"))
    result = service.handle_message(
        ChatMessageInput(
            message="Felipe Godoy, 1996-09-19",
            conversation_id=started.conversation.id,
        ),
    )

    assert result.intent == ChatReceptionistIntent.LIST_APPOINTMENTS
    assert "1." in result.reply
    assert "2." in result.reply
    assert "Dermatology with Dr. Emily Carter" in result.reply
    assert "Cardiology with Dr. Michael Reed" in result.reply
    chat_context = result.conversation.conversation_metadata["chat_context"]
    assert chat_context.get("resolved_patient_id") == str(patient.id)
    assert len(chat_context["offered_appointments"]) == 2
    assert chat_context_id_not_exposed(result.reply, chat_context=chat_context)


def test_lookup_includes_scheduled_future_appointments() -> None:
    service, _repository, patient, emily, _reed = _create_chat_service_with_patient()
    _add_appointments(
        service,
        [
            _wednesday_appointment(
                patient_id=patient.id,
                doctor_id=emily.id,
                specialty_id=emily.specialty_id,
                status=AppointmentStatus.SCHEDULED,
            ),
        ],
    )

    started = service.handle_message(ChatMessageInput(message="list my appointments"))
    result = service.handle_message(
        ChatMessageInput(
            message="Felipe Godoy, 1996-09-19",
            conversation_id=started.conversation.id,
        ),
    )

    assert "Dermatology with Dr. Emily Carter" in result.reply


def test_lookup_excludes_rescheduled_appointments() -> None:
    service, _repository, patient, emily, reed = _create_chat_service_with_patient()
    _add_appointments(
        service,
        [
            _wednesday_appointment(
                patient_id=patient.id,
                doctor_id=emily.id,
                specialty_id=emily.specialty_id,
                status=AppointmentStatus.RESCHEDULED,
            ),
            _friday_appointment(
                patient_id=patient.id,
                doctor_id=reed.id,
                specialty_id=reed.specialty_id,
            ),
        ],
    )

    started = service.handle_message(ChatMessageInput(message="my upcoming appointments"))
    result = service.handle_message(
        ChatMessageInput(
            message="Felipe Godoy, 1996-09-19",
            conversation_id=started.conversation.id,
        ),
    )

    assert "Dermatology" not in result.reply
    assert "Cardiology with Dr. Michael Reed" in result.reply
    chat_context = result.conversation.conversation_metadata["chat_context"]
    assert len(chat_context["offered_appointments"]) == 1


def test_lookup_excludes_cancelled_and_completed_appointments() -> None:
    service, _repository, patient, emily, reed = _create_chat_service_with_patient()
    _add_appointments(
        service,
        [
            _wednesday_appointment(
                patient_id=patient.id,
                doctor_id=emily.id,
                specialty_id=emily.specialty_id,
                status=AppointmentStatus.CANCELLED,
            ),
            _friday_appointment(
                patient_id=patient.id,
                doctor_id=reed.id,
                specialty_id=reed.specialty_id,
                status=AppointmentStatus.COMPLETED,
            ),
        ],
    )

    started = service.handle_message(ChatMessageInput(message="can I see my appointments?"))
    result = service.handle_message(
        ChatMessageInput(
            message="Felipe Godoy, 1996-09-19",
            conversation_id=started.conversation.id,
        ),
    )

    assert "don't see any upcoming scheduled appointments" in result.reply.lower()
    assert "book one" in result.reply.lower()
    chat_context = result.conversation.conversation_metadata["chat_context"]
    assert chat_context.get("offered_appointments") is None


def test_lookup_excludes_past_appointments() -> None:
    service, _repository, patient, emily, _reed = _create_chat_service_with_patient()
    past = create_appointment(
        patient_id=patient.id,
        doctor_id=emily.id,
        specialty_id=emily.specialty_id,
        start_time=datetime(2026, 6, 20, 14, 0, tzinfo=UTC),
        status=AppointmentStatus.SCHEDULED,
    )
    _add_appointments(service, [past])

    started = service.handle_message(ChatMessageInput(message="show my scheduled appointments"))
    result = service.handle_message(
        ChatMessageInput(
            message="Felipe Godoy, 1996-09-19",
            conversation_id=started.conversation.id,
        ),
    )

    assert "don't see any upcoming scheduled appointments" in result.reply.lower()


def test_lookup_no_appointments_offers_to_book() -> None:
    service, _repository, _patient, _emily, _reed = _create_chat_service_with_patient()

    started = service.handle_message(
        ChatMessageInput(message="what are my upcoming appointments?"),
    )
    result = service.handle_message(
        ChatMessageInput(
            message="Felipe Godoy, 1996-09-19",
            conversation_id=started.conversation.id,
        ),
    )

    assert "don't see any upcoming scheduled appointments" in result.reply.lower()
    assert "book one" in result.reply.lower()


def test_lookup_contextual_fallback_reprompts_partial_identity() -> None:
    resolved = _resolve_contextual_fallback_reply(
        {
            "appointment_management_mode": APPOINTMENT_MANAGEMENT_MODE_LOOKUP,
            "appointment_management_awaiting": APPOINTMENT_MANAGEMENT_AWAITING_PATIENT_IDENTITY,
            "appointment_management_identity": {"full_name": "Felipe Godoy"},
        },
    )

    assert resolved is not None
    intent, content = resolved
    assert intent == ChatReceptionistIntent.LIST_APPOINTMENTS
    assert "date of birth" in content.lower()
    assert "full name and date of birth" not in content.lower()


def test_fake_interpreter_classifies_list_appointments_separately() -> None:
    interpreter = FakeChatTurnUnderstandingInterpreter()
    result = interpreter.interpret(
        ChatTurnUnderstandingRequest(
            conversation_state=ConversationState.IDLE,
            expected_response_type=ExpectedResponseType.OPEN_TEXT,
            latest_user_message=_lookup_message(),
            allowed_intents=[
                ChatTurnIntent.APPOINTMENT_REQUEST,
                ChatTurnIntent.CANCEL_REQUEST,
                ChatTurnIntent.RESCHEDULE_REQUEST,
                ChatTurnIntent.LIST_APPOINTMENTS,
                ChatTurnIntent.FALLBACK,
            ],
        ),
    )

    assert result.intent is ChatTurnIntent.LIST_APPOINTMENTS
    assert result.intent not in {
        ChatTurnIntent.APPOINTMENT_REQUEST,
        ChatTurnIntent.CANCEL_REQUEST,
        ChatTurnIntent.RESCHEDULE_REQUEST,
    }


def test_lookup_completed_context_allows_cancel_follow_up() -> None:
    service, _repository, patient, emily, _reed = _create_chat_service_with_patient()
    _add_appointments(
        service,
        [
            _wednesday_appointment(
                patient_id=patient.id,
                doctor_id=emily.id,
                specialty_id=emily.specialty_id,
            ),
        ],
    )

    started = service.handle_message(ChatMessageInput(message="show my scheduled appointments"))
    listed = service.handle_message(
        ChatMessageInput(
            message="Felipe Godoy, 1996-09-19",
            conversation_id=started.conversation.id,
        ),
    )
    chat_context = listed.conversation.conversation_metadata["chat_context"]
    assert (
        chat_context["appointment_management_awaiting"]
        == APPOINTMENT_MANAGEMENT_AWAITING_COMPLETED
    )

    cancel = service.handle_message(
        ChatMessageInput(message="cancel it", conversation_id=started.conversation.id),
    )

    cancel_context = cancel.conversation.conversation_metadata["chat_context"]
    assert cancel_context.get("resolved_patient_id") == str(patient.id)
    assert "cancel" in cancel.reply.lower()
