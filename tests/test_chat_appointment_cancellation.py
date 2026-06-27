from __future__ import annotations

from datetime import UTC, date, datetime
from typing import cast
from unittest.mock import patch
from uuid import UUID, uuid4

import pytest

from app.domain.appointments import is_appointment_cancelable
from app.domain.scheduling.enums import AppointmentStatus
from app.models.scheduling import Appointment, Doctor, Patient
from app.services.appointment_cancellation import AppointmentCancellationService
from app.services.chat_appointment_cancellation import (
    APPOINTMENT_MANAGEMENT_AWAITING_APPOINTMENT_SELECTION,
    APPOINTMENT_MANAGEMENT_AWAITING_CANCELLATION_CONFIRMATION,
    APPOINTMENT_MANAGEMENT_AWAITING_COMPLETED,
    APPOINTMENT_MANAGEMENT_AWAITING_PATIENT_IDENTITY,
    APPOINTMENT_MANAGEMENT_MODE_CANCEL,
)
from app.services.chat_receptionist import (
    ChatMessageInput,
    ChatMessageResult,
    ChatReceptionistIntent,
    ChatReceptionistService,
)
from app.services.conversations import ConversationService
from app.services.post_cancellation_turn import (
    PostCancellationTurnDecision,
    classify_post_cancellation_turn,
)
from tests.clinic_time_test_support import REFERENCE_CLINIC_NOW_UTC
from tests.test_chat_receptionist_service import (
    create_chat_receptionist_service,
)
from tests.test_conversations import FakeConversationRepository
from tests.test_scheduling_services import (
    FakeAppointmentRepository,
    FakePatientRepository,
    create_appointment,
    create_service,
    create_specialty,
)


def _felipe_patient() -> Patient:
    return Patient(
        id=uuid4(),
        full_name="Felipe Godoy",
        date_of_birth=date(1996, 9, 19),
        phone_number="+1-555-0301",
        email="felipe.godoy@example.test",
    )


def _create_chat_service_with_patient() -> tuple[
    ChatReceptionistService,
    FakeConversationRepository,
    Patient,
    Doctor,
    Doctor,
]:
    dermatology = create_specialty(name="Dermatology")
    cardiology = create_specialty(name="Cardiology")
    emily = Doctor(
        id=uuid4(),
        specialty_id=dermatology.id,
        full_name="Dr. Emily Carter",
        email="emily.carter@example-clinic.test",
        phone_number="+1-555-0101",
        is_active=True,
    )
    reed = Doctor(
        id=uuid4(),
        specialty_id=cardiology.id,
        full_name="Dr. Michael Reed",
        email="michael.reed@example-clinic.test",
        phone_number="+1-555-0102",
        is_active=True,
    )
    patient = _felipe_patient()
    scheduling = create_service(
        specialties=[dermatology, cardiology],
        doctors=[emily, reed],
        patients=[patient],
        appointments=[],
    )
    repository = FakeConversationRepository()
    conversations = ConversationService(repository=repository)
    service = create_chat_receptionist_service(
        conversations=conversations,
        scheduling=scheduling,
    )
    return service, repository, patient, emily, reed


def _add_appointments(
    service: ChatReceptionistService,
    appointments: list[Appointment],
) -> None:
    repository = service.scheduling.appointments
    assert isinstance(repository, FakeAppointmentRepository)
    repository.appointments.extend(appointments)


def _wednesday_appointment(
    *,
    patient_id: UUID,
    doctor_id: UUID,
    specialty_id: UUID,
    status: AppointmentStatus = AppointmentStatus.SCHEDULED,
) -> Appointment:
    return create_appointment(
        patient_id=patient_id,
        doctor_id=doctor_id,
        specialty_id=specialty_id,
        start_time=datetime(2026, 7, 8, 14, 0, tzinfo=UTC),
        status=status,
    )


def _friday_appointment(
    *,
    patient_id: UUID,
    doctor_id: UUID,
    specialty_id: UUID,
    status: AppointmentStatus = AppointmentStatus.SCHEDULED,
) -> Appointment:
    return create_appointment(
        patient_id=patient_id,
        doctor_id=doctor_id,
        specialty_id=specialty_id,
        start_time=datetime(2026, 7, 10, 18, 0, tzinfo=UTC),
        status=status,
    )


def test_cancellation_identity_resolves_patient_and_lists_one_appointment() -> None:
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

    started = service.handle_message(ChatMessageInput(message="I want to cancel my appointment"))
    result = service.handle_message(
        ChatMessageInput(
            message="Felipe Godoy, 1996-09-19",
            conversation_id=started.conversation.id,
        ),
    )

    chat_context = result.conversation.conversation_metadata["chat_context"]
    assert result.intent == ChatReceptionistIntent.CANCEL_REQUEST
    assert chat_context["appointment_management_mode"] == APPOINTMENT_MANAGEMENT_MODE_CANCEL
    assert (
        chat_context["appointment_management_awaiting"]
        == APPOINTMENT_MANAGEMENT_AWAITING_CANCELLATION_CONFIRMATION
    )
    assert chat_context["resolved_patient_id"] == str(patient.id)
    assert chat_context["resolved_patient_name"] == "Felipe Godoy"
    assert chat_context["selected_appointment_id"]
    assert "Dermatology" in chat_context["selected_appointment_summary"]
    assert "Dr. Emily Carter" in chat_context["selected_appointment_summary"]
    assert "appointment you want to cancel" in result.reply


def test_cancellation_single_appointment_reply_asks_for_confirmation() -> None:
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

    started = service.handle_message(ChatMessageInput(message="cancel my appointment"))
    result = service.handle_message(
        ChatMessageInput(
            message="Felipe Godoy, 1996-09-19",
            conversation_id=started.conversation.id,
        ),
    )

    reply = result.reply
    assert "Dermatology appointment with Dr. Emily Carter" in reply
    assert "Wednesday" in reply
    assert "10:00" in reply
    assert "appointment you want to cancel" in reply
    assert str(patient.id) not in reply
    chat_context = result.conversation.conversation_metadata["chat_context"]
    assert chat_context_id_not_exposed(reply, chat_context=chat_context)


def _reach_appointment_selection(
    service: ChatReceptionistService,
    *,
    appointments: list[Appointment],
) -> tuple[ChatMessageResult, UUID]:
    _add_appointments(service, appointments)
    started = service.handle_message(ChatMessageInput(message="cancel my appointment"))
    result = service.handle_message(
        ChatMessageInput(
            message="Felipe Godoy, 1996-09-19",
            conversation_id=started.conversation.id,
        ),
    )
    chat_context = result.conversation.conversation_metadata["chat_context"]
    assert (
        chat_context["appointment_management_awaiting"]
        == APPOINTMENT_MANAGEMENT_AWAITING_APPOINTMENT_SELECTION
    )
    return result, started.conversation.id


def test_cancellation_selection_the_first_one_selects_first_appointment() -> None:
    service, _repository, patient, emily, reed = _create_chat_service_with_patient()
    selection_result, conversation_id = _reach_appointment_selection(
        service,
        appointments=[
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

    result = service.handle_message(
        ChatMessageInput(message="the first one", conversation_id=conversation_id),
    )

    chat_context = result.conversation.conversation_metadata["chat_context"]
    offered = selection_result.conversation.conversation_metadata["chat_context"][
        "offered_appointments"
    ]
    assert (
        chat_context["appointment_management_awaiting"]
        == APPOINTMENT_MANAGEMENT_AWAITING_CANCELLATION_CONFIRMATION
    )
    assert chat_context["selected_appointment_id"] == offered[0]["appointment_id"]
    assert "Dermatology" in chat_context["selected_appointment_summary"]
    assert "Please confirm: should I cancel your" in result.reply
    assert "Dermatology appointment with Dr. Emily Carter" in result.reply
    assert chat_context_id_not_exposed(result.reply, chat_context=chat_context)


def test_cancellation_selection_option_number_selects_first_appointment() -> None:
    service, _repository, patient, emily, reed = _create_chat_service_with_patient()
    _selection_result, conversation_id = _reach_appointment_selection(
        service,
        appointments=[
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

    result = service.handle_message(
        ChatMessageInput(message="1", conversation_id=conversation_id),
    )

    chat_context = result.conversation.conversation_metadata["chat_context"]
    assert (
        chat_context["appointment_management_awaiting"]
        == APPOINTMENT_MANAGEMENT_AWAITING_CANCELLATION_CONFIRMATION
    )
    assert "Dermatology" in chat_context["selected_appointment_summary"]
    assert "Please confirm: should I cancel your" in result.reply


def test_cancellation_selection_specialty_selects_matching_appointment() -> None:
    service, _repository, patient, emily, reed = _create_chat_service_with_patient()
    _selection_result, conversation_id = _reach_appointment_selection(
        service,
        appointments=[
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

    result = service.handle_message(
        ChatMessageInput(message="the dermatology one", conversation_id=conversation_id),
    )

    chat_context = result.conversation.conversation_metadata["chat_context"]
    assert (
        chat_context["appointment_management_awaiting"]
        == APPOINTMENT_MANAGEMENT_AWAITING_CANCELLATION_CONFIRMATION
    )
    assert "Dermatology" in chat_context["selected_appointment_summary"]
    assert "Cardiology" not in chat_context["selected_appointment_summary"]


def test_cancellation_selection_doctor_name_selects_matching_appointment() -> None:
    service, _repository, patient, emily, reed = _create_chat_service_with_patient()
    _selection_result, conversation_id = _reach_appointment_selection(
        service,
        appointments=[
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

    result = service.handle_message(
        ChatMessageInput(message="Dr. Emily", conversation_id=conversation_id),
    )

    chat_context = result.conversation.conversation_metadata["chat_context"]
    assert (
        chat_context["appointment_management_awaiting"]
        == APPOINTMENT_MANAGEMENT_AWAITING_CANCELLATION_CONFIRMATION
    )
    assert "Dr. Emily Carter" in chat_context["selected_appointment_summary"]


def test_cancellation_selection_weekday_selects_unique_appointment() -> None:
    service, _repository, patient, emily, reed = _create_chat_service_with_patient()
    _selection_result, conversation_id = _reach_appointment_selection(
        service,
        appointments=[
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

    result = service.handle_message(
        ChatMessageInput(message="Wednesday", conversation_id=conversation_id),
    )

    chat_context = result.conversation.conversation_metadata["chat_context"]
    assert (
        chat_context["appointment_management_awaiting"]
        == APPOINTMENT_MANAGEMENT_AWAITING_CANCELLATION_CONFIRMATION
    )
    assert "Wednesday" in chat_context["selected_appointment_summary"]


def test_cancellation_selection_time_selects_unique_appointment() -> None:
    service, _repository, patient, emily, reed = _create_chat_service_with_patient()
    _selection_result, conversation_id = _reach_appointment_selection(
        service,
        appointments=[
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

    result = service.handle_message(
        ChatMessageInput(message="10 AM", conversation_id=conversation_id),
    )

    chat_context = result.conversation.conversation_metadata["chat_context"]
    assert (
        chat_context["appointment_management_awaiting"]
        == APPOINTMENT_MANAGEMENT_AWAITING_CANCELLATION_CONFIRMATION
    )
    assert "10:00" in chat_context["selected_appointment_summary"]


def test_cancellation_selection_success_preserves_patient_context() -> None:
    service, _repository, patient, emily, reed = _create_chat_service_with_patient()
    _selection_result, conversation_id = _reach_appointment_selection(
        service,
        appointments=[
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

    result = service.handle_message(
        ChatMessageInput(message="number 1", conversation_id=conversation_id),
    )

    chat_context = result.conversation.conversation_metadata["chat_context"]
    assert chat_context["resolved_patient_id"] == str(patient.id)
    assert chat_context["resolved_patient_name"] == "Felipe Godoy"
    assert chat_context.get("patient_resolution_id")
    assert chat_context.get("offered_appointments")
    assert chat_context.get("selected_appointment_id")
    assert chat_context.get("selected_appointment_summary")


def test_cancellation_selection_zero_match_reprompts_without_selecting() -> None:
    service, _repository, patient, emily, reed = _create_chat_service_with_patient()
    _selection_result, conversation_id = _reach_appointment_selection(
        service,
        appointments=[
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

    result = service.handle_message(
        ChatMessageInput(message="the oncology one", conversation_id=conversation_id),
    )

    chat_context = result.conversation.conversation_metadata["chat_context"]
    assert (
        chat_context["appointment_management_awaiting"]
        == APPOINTMENT_MANAGEMENT_AWAITING_APPOINTMENT_SELECTION
    )
    assert chat_context.get("selected_appointment_id") is None
    assert "Please choose one of the appointments I listed." in result.reply


def test_cancellation_selection_ambiguous_match_asks_clarification() -> None:
    service, _repository, patient, emily, _reed = _create_chat_service_with_patient()
    second_wednesday = create_appointment(
        patient_id=patient.id,
        doctor_id=emily.id,
        specialty_id=emily.specialty_id,
        start_time=datetime(2026, 7, 15, 15, 0, tzinfo=UTC),
        status=AppointmentStatus.SCHEDULED,
    )
    _selection_result, conversation_id = _reach_appointment_selection(
        service,
        appointments=[
            _wednesday_appointment(
                patient_id=patient.id,
                doctor_id=emily.id,
                specialty_id=emily.specialty_id,
            ),
            second_wednesday,
        ],
    )

    result = service.handle_message(
        ChatMessageInput(message="Wednesday", conversation_id=conversation_id),
    )

    chat_context = result.conversation.conversation_metadata["chat_context"]
    assert (
        chat_context["appointment_management_awaiting"]
        == APPOINTMENT_MANAGEMENT_AWAITING_APPOINTMENT_SELECTION
    )
    assert chat_context.get("selected_appointment_id") is None
    assert "more than one matching appointment" in result.reply


def test_cancellation_selection_does_not_call_cancellation_service() -> None:
    service, _repository, patient, emily, reed = _create_chat_service_with_patient()
    _selection_result, conversation_id = _reach_appointment_selection(
        service,
        appointments=[
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

    with patch.object(
        AppointmentCancellationService,
        "cancel_appointment",
    ) as cancel_appointment_mock:
        service.handle_message(
            ChatMessageInput(message="the first one", conversation_id=conversation_id),
        )

    cancel_appointment_mock.assert_not_called()


def test_cancellation_selection_cancel_keyword_reprompts_in_selection_frame() -> None:
    service, _repository, patient, emily, reed = _create_chat_service_with_patient()
    _selection_result, conversation_id = _reach_appointment_selection(
        service,
        appointments=[
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

    result = service.handle_message(
        ChatMessageInput(message="I need to cancel", conversation_id=conversation_id),
    )

    chat_context = result.conversation.conversation_metadata["chat_context"]
    assert chat_context["appointment_management_mode"] == APPOINTMENT_MANAGEMENT_MODE_CANCEL
    assert (
        chat_context["appointment_management_awaiting"]
        == APPOINTMENT_MANAGEMENT_AWAITING_APPOINTMENT_SELECTION
    )
    assert chat_context.get("selected_appointment_id") is None
    assert "Which appointment would you like to cancel?" in result.reply


def test_cancellation_multiple_appointments_sets_selection_context() -> None:
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

    started = service.handle_message(ChatMessageInput(message="I need to cancel"))
    result = service.handle_message(
        ChatMessageInput(
            message="Felipe Godoy, 1996-09-19",
            conversation_id=started.conversation.id,
        ),
    )

    chat_context = result.conversation.conversation_metadata["chat_context"]
    assert (
        chat_context["appointment_management_awaiting"]
        == APPOINTMENT_MANAGEMENT_AWAITING_APPOINTMENT_SELECTION
    )
    offered = chat_context["offered_appointments"]
    assert len(offered) == 2
    assert all("appointment_id" in item and "summary" in item for item in offered)
    assert "1." in result.reply
    assert "2." in result.reply
    assert "Dermatology with Dr. Emily Carter" in result.reply
    assert "Cardiology with Dr. Michael Reed" in result.reply
    assert "Which one would you like to cancel?" in result.reply
    assert chat_context_id_not_exposed(result.reply, chat_context=chat_context)


def test_cancellation_no_appointments_returns_guidance_and_keeps_identity_awaiting() -> None:
    service, _repository, _patient, _emily, _reed = _create_chat_service_with_patient()

    started = service.handle_message(ChatMessageInput(message="cancel my appointment"))
    result = service.handle_message(
        ChatMessageInput(
            message="Felipe Godoy, 1996-09-19",
            conversation_id=started.conversation.id,
        ),
    )

    chat_context = result.conversation.conversation_metadata["chat_context"]
    assert "not seeing any upcoming appointments" in result.reply.lower()
    assert chat_context["appointment_management_mode"] == APPOINTMENT_MANAGEMENT_MODE_CANCEL
    assert (
        chat_context["appointment_management_awaiting"]
        == APPOINTMENT_MANAGEMENT_AWAITING_PATIENT_IDENTITY
    )
    assert chat_context.get("selected_appointment_id") is None
    assert chat_context.get("offered_appointments") is None


def test_cancellation_incomplete_identity_reprompts() -> None:
    service, _repository, _patient, _emily, _reed = _create_chat_service_with_patient()

    started = service.handle_message(ChatMessageInput(message="cancel my appointment"))
    result = service.handle_message(
        ChatMessageInput(message="Felipe Godoy", conversation_id=started.conversation.id),
    )

    assert "full name" in result.reply.lower()
    assert "date of birth" in result.reply.lower()
    chat_context = result.conversation.conversation_metadata["chat_context"]
    assert (
        chat_context["appointment_management_awaiting"]
        == APPOINTMENT_MANAGEMENT_AWAITING_PATIENT_IDENTITY
    )


def test_cancellation_patient_not_found_does_not_list_appointments() -> None:
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

    started = service.handle_message(ChatMessageInput(message="cancel my appointment"))
    result = service.handle_message(
        ChatMessageInput(
            message="Unknown Person, 1990-01-01",
            conversation_id=started.conversation.id,
        ),
    )

    assert "couldn't find a matching patient profile" in result.reply.lower()
    chat_context = result.conversation.conversation_metadata["chat_context"]
    assert chat_context.get("offered_appointments") is None
    assert chat_context.get("selected_appointment_id") is None
    assert (
        chat_context["appointment_management_awaiting"]
        == APPOINTMENT_MANAGEMENT_AWAITING_PATIENT_IDENTITY
    )


def test_cancellation_ambiguous_numeric_dob_asks_clarification_without_listing() -> None:
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

    started = service.handle_message(ChatMessageInput(message="cancel my appointment"))
    result = service.handle_message(
        ChatMessageInput(
            message="Felipe Godoy, 09/08/1980",
            conversation_id=started.conversation.id,
        ),
    )

    reply = result.reply.lower()
    assert result.intent == ChatReceptionistIntent.CANCEL_REQUEST
    assert "september 8, 1980" in reply
    assert "august 9, 1980" in reply
    chat_context = result.conversation.conversation_metadata["chat_context"]
    # No patient lookup happened, so no appointments were listed.
    assert chat_context.get("offered_appointments") is None
    assert chat_context.get("selected_appointment_id") is None
    assert chat_context.get("resolved_patient_id") is None
    assert (
        chat_context["appointment_management_awaiting"]
        == APPOINTMENT_MANAGEMENT_AWAITING_PATIENT_IDENTITY
    )


def _reach_cancellation_confirmation(
    service: ChatReceptionistService,
    *,
    appointments: list[Appointment],
) -> tuple[ChatMessageResult, UUID, Appointment]:
    _add_appointments(service, appointments)
    started = service.handle_message(ChatMessageInput(message="cancel my appointment"))
    result = service.handle_message(
        ChatMessageInput(
            message="Felipe Godoy, 1996-09-19",
            conversation_id=started.conversation.id,
        ),
    )
    chat_context = result.conversation.conversation_metadata["chat_context"]
    assert (
        chat_context["appointment_management_awaiting"]
        == APPOINTMENT_MANAGEMENT_AWAITING_CANCELLATION_CONFIRMATION
    )
    appointment = appointments[0]
    return result, started.conversation.id, appointment


def test_cancellation_confirmation_yes_calls_cancellation_service() -> None:
    service, _repository, patient, emily, _reed = _create_chat_service_with_patient()
    appointment = _wednesday_appointment(
        patient_id=patient.id,
        doctor_id=emily.id,
        specialty_id=emily.specialty_id,
    )
    _reach_cancellation_confirmation_result, conversation_id, _appointment = (
        _reach_cancellation_confirmation(service, appointments=[appointment])
    )

    with patch.object(
        AppointmentCancellationService,
        "cancel_appointment",
        wraps=service._appointment_cancellation.appointment_cancellation.cancel_appointment,
    ) as cancel_appointment_mock:
        result = service.handle_message(
            ChatMessageInput(message="yes", conversation_id=conversation_id),
        )

    cancel_appointment_mock.assert_called_once()
    request = cancel_appointment_mock.call_args.args[0]
    assert request.appointment_id == appointment.id
    assert request.explicit_confirmation is True
    assert request.idempotency_key == f"chat-cancel:{conversation_id}:{appointment.id}"
    assert "has been cancelled" in result.reply
    chat_context = result.conversation.conversation_metadata["chat_context"]
    assert (
        chat_context["appointment_management_awaiting"]
        == APPOINTMENT_MANAGEMENT_AWAITING_COMPLETED
    )
    assert chat_context["cancellation_status"] == "cancelled"
    assert appointment.status == AppointmentStatus.CANCELLED


def test_cancellation_confirmation_cancels_only_selected_appointment() -> None:
    service, _repository, patient, emily, reed = _create_chat_service_with_patient()
    dermatology = _wednesday_appointment(
        patient_id=patient.id,
        doctor_id=emily.id,
        specialty_id=emily.specialty_id,
    )
    cardiology = _friday_appointment(
        patient_id=patient.id,
        doctor_id=reed.id,
        specialty_id=reed.specialty_id,
    )
    _selection_result, conversation_id = _reach_appointment_selection(
        service,
        appointments=[dermatology, cardiology],
    )
    service.handle_message(
        ChatMessageInput(message="the first one", conversation_id=conversation_id),
    )

    service.handle_message(
        ChatMessageInput(message="yes please", conversation_id=conversation_id),
    )

    assert dermatology.status == AppointmentStatus.CANCELLED
    assert cardiology.status == AppointmentStatus.SCHEDULED


def test_cancellation_confirmation_success_message_format() -> None:
    service, _repository, patient, emily, _reed = _create_chat_service_with_patient()
    appointment = _wednesday_appointment(
        patient_id=patient.id,
        doctor_id=emily.id,
        specialty_id=emily.specialty_id,
    )
    _reach_cancellation_confirmation_result, conversation_id, _appointment = (
        _reach_cancellation_confirmation(service, appointments=[appointment])
    )

    result = service.handle_message(
        ChatMessageInput(message="please cancel it", conversation_id=conversation_id),
    )

    assert (
        "Your Dermatology appointment with Dr. Emily Carter on Wednesday at 10:00 "
        "has been cancelled. Is there anything else I can help with?"
    ) in result.reply
    chat_context = result.conversation.conversation_metadata["chat_context"]
    assert chat_context_id_not_exposed(result.reply, chat_context=chat_context)


def test_cancellation_confirmation_success_sets_completed_context() -> None:
    service, _repository, patient, emily, _reed = _create_chat_service_with_patient()
    appointment = _wednesday_appointment(
        patient_id=patient.id,
        doctor_id=emily.id,
        specialty_id=emily.specialty_id,
    )
    _reach_cancellation_confirmation_result, conversation_id, _appointment = (
        _reach_cancellation_confirmation(service, appointments=[appointment])
    )

    result = service.handle_message(
        ChatMessageInput(message="confirm", conversation_id=conversation_id),
    )

    chat_context = result.conversation.conversation_metadata["chat_context"]
    assert (
        chat_context["appointment_management_awaiting"]
        == APPOINTMENT_MANAGEMENT_AWAITING_COMPLETED
    )
    assert chat_context["cancellation_status"] == "cancelled"
    assert chat_context.get("selected_appointment_id") is None
    assert chat_context.get("selected_appointment_summary") is None
    assert chat_context.get("offered_appointments") is None
    assert chat_context.get("cancelled_appointment_summary")
    assert "Dermatology" in result.reply


def test_cancellation_confirmation_rejection_does_not_call_service() -> None:
    service, _repository, patient, emily, _reed = _create_chat_service_with_patient()
    appointment = _wednesday_appointment(
        patient_id=patient.id,
        doctor_id=emily.id,
        specialty_id=emily.specialty_id,
    )
    _reach_cancellation_confirmation_result, conversation_id, _appointment = (
        _reach_cancellation_confirmation(service, appointments=[appointment])
    )

    with patch.object(
        AppointmentCancellationService,
        "cancel_appointment",
    ) as cancel_appointment_mock:
        result = service.handle_message(
            ChatMessageInput(message="no", conversation_id=conversation_id),
        )

    cancel_appointment_mock.assert_not_called()
    assert appointment.status == AppointmentStatus.SCHEDULED
    assert "won't cancel" in result.reply.lower()


def test_cancellation_confirmation_rejection_sets_declined_context() -> None:
    service, _repository, patient, emily, _reed = _create_chat_service_with_patient()
    appointment = _wednesday_appointment(
        patient_id=patient.id,
        doctor_id=emily.id,
        specialty_id=emily.specialty_id,
    )
    _reach_cancellation_confirmation_result, conversation_id, _appointment = (
        _reach_cancellation_confirmation(service, appointments=[appointment])
    )

    result = service.handle_message(
        ChatMessageInput(message="keep it", conversation_id=conversation_id),
    )

    chat_context = result.conversation.conversation_metadata["chat_context"]
    assert (
        chat_context["appointment_management_awaiting"]
        == APPOINTMENT_MANAGEMENT_AWAITING_COMPLETED
    )
    assert chat_context["cancellation_status"] == "declined"
    assert chat_context.get("selected_appointment_id") is None


def test_cancellation_confirmation_ambiguous_reprompts_without_service_call() -> None:
    service, _repository, patient, emily, _reed = _create_chat_service_with_patient()
    appointment = _wednesday_appointment(
        patient_id=patient.id,
        doctor_id=emily.id,
        specialty_id=emily.specialty_id,
    )
    _reach_cancellation_confirmation_result, conversation_id, _appointment = (
        _reach_cancellation_confirmation(service, appointments=[appointment])
    )

    with patch.object(
        AppointmentCancellationService,
        "cancel_appointment",
    ) as cancel_appointment_mock:
        result = service.handle_message(
            ChatMessageInput(message="maybe", conversation_id=conversation_id),
        )

    cancel_appointment_mock.assert_not_called()
    chat_context = result.conversation.conversation_metadata["chat_context"]
    assert (
        chat_context["appointment_management_awaiting"]
        == APPOINTMENT_MANAGEMENT_AWAITING_CANCELLATION_CONFIRMATION
    )
    assert chat_context.get("selected_appointment_id")
    assert "Please confirm whether you want me to cancel your" in result.reply
    assert "Dermatology appointment with Dr. Emily Carter" in result.reply


def test_cancellation_confirmation_ownership_mismatch_does_not_cancel() -> None:
    service, _repository, patient, emily, _reed = _create_chat_service_with_patient()
    other_patient = Patient(
        id=uuid4(),
        full_name="Other Patient",
        date_of_birth=date(1990, 1, 1),
        phone_number="+1-555-9999",
        email="other@example.test",
    )
    cast(FakePatientRepository, service.scheduling.patients).patients.append(other_patient)
    wrong_owner_appointment = _wednesday_appointment(
        patient_id=other_patient.id,
        doctor_id=emily.id,
        specialty_id=emily.specialty_id,
    )
    _reach_cancellation_confirmation_result, conversation_id, _appointment = (
        _reach_cancellation_confirmation(
            service,
            appointments=[
                _wednesday_appointment(
                    patient_id=patient.id,
                    doctor_id=emily.id,
                    specialty_id=emily.specialty_id,
                ),
            ],
        )
    )
    repository = service.scheduling.appointments
    assert isinstance(repository, FakeAppointmentRepository)
    repository.appointments.append(wrong_owner_appointment)
    conversation = service.conversations.get_conversation(conversation_id)
    service.conversations.merge_chat_context(
        conversation_id=conversation_id,
        chat_context={
            **conversation.conversation_metadata["chat_context"],
            "selected_appointment_id": str(wrong_owner_appointment.id),
        },
    )

    with patch.object(
        AppointmentCancellationService,
        "cancel_appointment",
    ) as cancel_appointment_mock:
        result = service.handle_message(
            ChatMessageInput(message="yes", conversation_id=conversation_id),
        )

    cancel_appointment_mock.assert_not_called()
    assert "couldn't verify that appointment" in result.reply.lower()
    assert str(wrong_owner_appointment.id) not in result.reply
    assert str(patient.id) not in result.reply


def test_cancellation_confirmation_missing_appointment_does_not_cancel() -> None:
    service, _repository, patient, emily, _reed = _create_chat_service_with_patient()
    appointment = _wednesday_appointment(
        patient_id=patient.id,
        doctor_id=emily.id,
        specialty_id=emily.specialty_id,
    )
    _reach_cancellation_confirmation_result, conversation_id, _appointment = (
        _reach_cancellation_confirmation(service, appointments=[appointment])
    )
    repository = service.scheduling.appointments
    assert isinstance(repository, FakeAppointmentRepository)
    repository.appointments.clear()

    with patch.object(
        AppointmentCancellationService,
        "cancel_appointment",
    ) as cancel_appointment_mock:
        result = service.handle_message(
            ChatMessageInput(message="yes", conversation_id=conversation_id),
        )

    cancel_appointment_mock.assert_not_called()
    assert "couldn't find that appointment" in result.reply.lower()


def test_cancellation_confirmation_not_cancelable_returns_safe_response() -> None:
    service, _repository, patient, emily, _reed = _create_chat_service_with_patient()
    appointment = _wednesday_appointment(
        patient_id=patient.id,
        doctor_id=emily.id,
        specialty_id=emily.specialty_id,
    )
    _reach_cancellation_confirmation_result, conversation_id, confirmed_appointment = (
        _reach_cancellation_confirmation(service, appointments=[appointment])
    )
    confirmed_appointment.status = AppointmentStatus.COMPLETED

    with patch.object(
        AppointmentCancellationService,
        "cancel_appointment",
    ) as cancel_appointment_mock:
        result = service.handle_message(
            ChatMessageInput(message="yes", conversation_id=conversation_id),
        )

    cancel_appointment_mock.assert_not_called()
    assert "can no longer be cancelled" in result.reply.lower()


def test_cancellation_confirmation_duplicate_is_idempotent() -> None:
    service, _repository, patient, emily, _reed = _create_chat_service_with_patient()
    appointment = _wednesday_appointment(
        patient_id=patient.id,
        doctor_id=emily.id,
        specialty_id=emily.specialty_id,
    )
    _reach_cancellation_confirmation_result, conversation_id, _appointment = (
        _reach_cancellation_confirmation(service, appointments=[appointment])
    )

    first = service.handle_message(
        ChatMessageInput(message="yes", conversation_id=conversation_id),
    )
    service.conversations.merge_chat_context(
        conversation_id=conversation_id,
        chat_context={
            "appointment_management_mode": APPOINTMENT_MANAGEMENT_MODE_CANCEL,
            "appointment_management_awaiting": (
                APPOINTMENT_MANAGEMENT_AWAITING_CANCELLATION_CONFIRMATION
            ),
            "resolved_patient_id": str(patient.id),
            "selected_appointment_id": str(appointment.id),
            "selected_appointment_summary": (
                "Dermatology with Dr. Emily Carter on Wednesday at 10:00"
            ),
        },
    )
    second = service.handle_message(
        ChatMessageInput(message="yes please", conversation_id=conversation_id),
    )

    assert appointment.status == AppointmentStatus.CANCELLED
    assert "cancelled" in first.reply.lower()
    assert "already been cancelled" in second.reply.lower()


def _complete_cancellation(
    service: ChatReceptionistService,
    *,
    appointments: list[Appointment],
) -> tuple[ChatMessageResult, UUID, Appointment]:
    _reach_result, conversation_id, appointment = _reach_cancellation_confirmation(
        service,
        appointments=appointments,
    )
    result = service.handle_message(
        ChatMessageInput(message="yes", conversation_id=conversation_id),
    )
    chat_context = result.conversation.conversation_metadata["chat_context"]
    assert chat_context["cancellation_status"] == "cancelled"
    return result, conversation_id, appointment


def test_cancellation_success_response_includes_follow_up_prompt() -> None:
    service, _repository, patient, emily, _reed = _create_chat_service_with_patient()
    result, _conversation_id, _appointment = _complete_cancellation(
        service,
        appointments=[
            _wednesday_appointment(
                patient_id=patient.id,
                doctor_id=emily.id,
                specialty_id=emily.specialty_id,
            ),
        ],
    )

    assert "has been cancelled" in result.reply
    assert "Is there anything else I can help with?" in result.reply


@pytest.mark.parametrize(
    "message",
    [
        "no",
        "nah",
        "no thanks",
        "that's all",
        "nothing else",
        "I'm good",
    ],
)
def test_post_cancellation_end_conversation_decision_closes_politely(
    message: str,
) -> None:
    assert (
        classify_post_cancellation_turn(message=message).decision
        is PostCancellationTurnDecision.END_CONVERSATION
    )

    service, _repository, patient, emily, _reed = _create_chat_service_with_patient()
    _cancel_result, conversation_id, _appointment = _complete_cancellation(
        service,
        appointments=[
            _wednesday_appointment(
                patient_id=patient.id,
                doctor_id=emily.id,
                specialty_id=emily.specialty_id,
            ),
        ],
    )

    result = service.handle_message(
        ChatMessageInput(message=message, conversation_id=conversation_id),
    )

    assert "You're all set. Have a great day!" in result.reply
    assert result.intent == ChatReceptionistIntent.CANCEL_REQUEST


@pytest.mark.parametrize(
    "message",
    [
        "yes",
        "yes please",
        "I need help",
        "actually yes",
    ],
)
def test_post_cancellation_needs_more_help_decision_prompts_next_action(
    message: str,
) -> None:
    assert (
        classify_post_cancellation_turn(message=message).decision
        is PostCancellationTurnDecision.NEEDS_MORE_HELP
    )

    service, _repository, patient, emily, _reed = _create_chat_service_with_patient()
    _cancel_result, conversation_id, _appointment = _complete_cancellation(
        service,
        appointments=[
            _wednesday_appointment(
                patient_id=patient.id,
                doctor_id=emily.id,
                specialty_id=emily.specialty_id,
            ),
        ],
    )

    result = service.handle_message(
        ChatMessageInput(message=message, conversation_id=conversation_id),
    )

    assert "schedule, cancel, or reschedule" in result.reply.lower()
    assert "You're all set" not in result.reply


@pytest.mark.parametrize(
    "message",
    [
        "I want to schedule an appointment",
        "I need another appointment",
    ],
)
def test_post_cancellation_new_scheduling_decision_allows_normal_routing(
    message: str,
) -> None:
    assert (
        classify_post_cancellation_turn(message=message).decision
        is PostCancellationTurnDecision.NEW_SCHEDULING_REQUEST
    )

    service, _repository, patient, emily, _reed = _create_chat_service_with_patient()
    _cancel_result, conversation_id, _appointment = _complete_cancellation(
        service,
        appointments=[
            _wednesday_appointment(
                patient_id=patient.id,
                doctor_id=emily.id,
                specialty_id=emily.specialty_id,
            ),
        ],
    )

    result = service.handle_message(
        ChatMessageInput(message=message, conversation_id=conversation_id),
    )

    assert result.intent != ChatReceptionistIntent.FALLBACK
    assert "You're all set" not in result.reply
    context = result.conversation.conversation_metadata["chat_context"]
    assert context.get("appointment_intake_awaiting") or "appointment" in result.reply.lower()


def test_post_cancellation_cancel_request_decision_enters_cancellation_frame() -> None:
    message = "I want to cancel another appointment"
    assert (
        classify_post_cancellation_turn(message=message).decision
        is PostCancellationTurnDecision.CANCEL_REQUEST
    )

    service, _repository, patient, emily, _reed = _create_chat_service_with_patient()
    _cancel_result, conversation_id, _appointment = _complete_cancellation(
        service,
        appointments=[
            _wednesday_appointment(
                patient_id=patient.id,
                doctor_id=emily.id,
                specialty_id=emily.specialty_id,
            ),
        ],
    )

    result = service.handle_message(
        ChatMessageInput(message=message, conversation_id=conversation_id),
    )

    assert result.intent == ChatReceptionistIntent.CANCEL_REQUEST
    context = result.conversation.conversation_metadata["chat_context"]
    assert context["appointment_management_mode"] == APPOINTMENT_MANAGEMENT_MODE_CANCEL
    assert (
        context["appointment_management_awaiting"]
        == APPOINTMENT_MANAGEMENT_AWAITING_PATIENT_IDENTITY
    )
    assert "full name" in result.reply.lower()


def test_post_cancellation_unknown_decision_returns_gentle_help_prompt() -> None:
    message = "Can you tell me a joke?"
    assert (
        classify_post_cancellation_turn(message=message).decision
        is PostCancellationTurnDecision.UNKNOWN
    )

    service, _repository, patient, emily, _reed = _create_chat_service_with_patient()
    _cancel_result, conversation_id, _appointment = _complete_cancellation(
        service,
        appointments=[
            _wednesday_appointment(
                patient_id=patient.id,
                doctor_id=emily.id,
                specialty_id=emily.specialty_id,
            ),
        ],
    )

    with patch.object(
        AppointmentCancellationService,
        "cancel_appointment",
    ) as cancel_appointment_mock:
        result = service.handle_message(
            ChatMessageInput(message=message, conversation_id=conversation_id),
        )

    cancel_appointment_mock.assert_not_called()
    assert result.intent == ChatReceptionistIntent.CANCEL_REQUEST
    assert "has been cancelled" in result.reply.lower()
    assert "schedule, cancel, or reschedule" in result.reply.lower()


def test_post_cancellation_completed_flow_does_not_reexecute_cancellation_service() -> None:
    service, _repository, patient, emily, _reed = _create_chat_service_with_patient()
    _cancel_result, conversation_id, _appointment = _complete_cancellation(
        service,
        appointments=[
            _wednesday_appointment(
                patient_id=patient.id,
                doctor_id=emily.id,
                specialty_id=emily.specialty_id,
            ),
        ],
    )

    with patch.object(
        AppointmentCancellationService,
        "cancel_appointment",
    ) as cancel_appointment_mock:
        service.handle_message(
            ChatMessageInput(message="no thanks", conversation_id=conversation_id),
        )

    cancel_appointment_mock.assert_not_called()


def test_cancellation_flow_does_not_call_cancellation_service() -> None:
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

    with patch.object(
        AppointmentCancellationService,
        "cancel_appointment",
    ) as cancel_appointment_mock:
        started = service.handle_message(ChatMessageInput(message="cancel my appointment"))
        service.handle_message(
            ChatMessageInput(
                message="Felipe Godoy, 1996-09-19",
                conversation_id=started.conversation.id,
            ),
        )

    cancel_appointment_mock.assert_not_called()


def test_fake_repository_list_cancelable_for_patient_excludes_rescheduled() -> None:
    patient_id = uuid4()
    doctor_id = uuid4()
    specialty_id = uuid4()
    scheduled = create_appointment(
        patient_id=patient_id,
        doctor_id=doctor_id,
        specialty_id=specialty_id,
        start_time=datetime(2026, 7, 8, 14, 0, tzinfo=UTC),
        status=AppointmentStatus.SCHEDULED,
    )
    rescheduled = create_appointment(
        patient_id=patient_id,
        doctor_id=doctor_id,
        specialty_id=specialty_id,
        start_time=datetime(2026, 7, 10, 18, 0, tzinfo=UTC),
        status=AppointmentStatus.RESCHEDULED,
    )
    cancelled = create_appointment(
        patient_id=patient_id,
        doctor_id=doctor_id,
        specialty_id=specialty_id,
        start_time=datetime(2026, 7, 11, 18, 0, tzinfo=UTC),
        status=AppointmentStatus.CANCELLED,
    )
    repository = FakeAppointmentRepository([scheduled, rescheduled, cancelled])

    appointments = repository.list_cancelable_for_patient(
        patient_id=patient_id,
        start_from=REFERENCE_CLINIC_NOW_UTC,
    )

    assert [appointment.id for appointment in appointments] == [scheduled.id]


def test_fake_repository_list_cancelable_for_patient_orders_by_start_time() -> None:
    patient_id = uuid4()
    doctor_id = uuid4()
    specialty_id = uuid4()
    later = create_appointment(
        patient_id=patient_id,
        doctor_id=doctor_id,
        specialty_id=specialty_id,
        start_time=datetime(2026, 7, 10, 18, 0, tzinfo=UTC),
        status=AppointmentStatus.SCHEDULED,
    )
    earlier = create_appointment(
        patient_id=patient_id,
        doctor_id=doctor_id,
        specialty_id=specialty_id,
        start_time=datetime(2026, 7, 8, 14, 0, tzinfo=UTC),
        status=AppointmentStatus.SCHEDULED,
    )
    repository = FakeAppointmentRepository([later, earlier])

    appointments = repository.list_cancelable_for_patient(
        patient_id=patient_id,
        start_from=REFERENCE_CLINIC_NOW_UTC,
    )

    assert [appointment.id for appointment in appointments] == [earlier.id, later.id]


def test_after_reschedule_only_successor_is_listed_and_predecessor_not_cancelable() -> None:
    patient_id = uuid4()
    doctor_id = uuid4()
    specialty_id = uuid4()
    monday_predecessor = create_appointment(
        patient_id=patient_id,
        doctor_id=doctor_id,
        specialty_id=specialty_id,
        start_time=datetime(2026, 7, 6, 14, 0, tzinfo=UTC),
        status=AppointmentStatus.RESCHEDULED,
    )
    tuesday_successor = create_appointment(
        patient_id=patient_id,
        doctor_id=doctor_id,
        specialty_id=specialty_id,
        start_time=datetime(2026, 7, 7, 19, 0, tzinfo=UTC),
        status=AppointmentStatus.SCHEDULED,
    )
    repository = FakeAppointmentRepository([monday_predecessor, tuesday_successor])

    cancelable = repository.list_cancelable_for_patient(
        patient_id=patient_id,
        start_from=REFERENCE_CLINIC_NOW_UTC,
    )

    assert [appointment.id for appointment in cancelable] == [tuesday_successor.id]
    assert is_appointment_cancelable(tuesday_successor.status) is True
    assert is_appointment_cancelable(monday_predecessor.status) is False


def chat_context_id_not_exposed(reply: str, *, chat_context: dict[str, object]) -> bool:
    id_values: set[object] = {
        chat_context.get("resolved_patient_id"),
        chat_context.get("selected_appointment_id"),
    }
    offered = chat_context.get("offered_appointments")
    if isinstance(offered, list):
        for item in offered:
            if isinstance(item, dict):
                id_values.add(item.get("appointment_id"))
    for value in id_values:
        if value and str(value) in reply:
            return False
    return True
