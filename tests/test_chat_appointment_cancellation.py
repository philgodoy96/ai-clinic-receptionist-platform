from __future__ import annotations

from datetime import UTC, date, datetime
from unittest.mock import patch
from uuid import UUID, uuid4

from app.domain.scheduling.enums import AppointmentStatus
from app.models.scheduling import Appointment, Doctor, Patient
from app.services.appointment_cancellation import AppointmentCancellationService
from app.services.chat_appointment_cancellation import (
    APPOINTMENT_MANAGEMENT_AWAITING_APPOINTMENT_SELECTION,
    APPOINTMENT_MANAGEMENT_AWAITING_CANCELLATION_CONFIRMATION,
    APPOINTMENT_MANAGEMENT_AWAITING_PATIENT_IDENTITY,
    APPOINTMENT_MANAGEMENT_MODE_CANCEL,
)
from app.services.chat_receptionist import (
    ChatMessageInput,
    ChatReceptionistIntent,
    ChatReceptionistService,
)
from app.services.conversations import ConversationService
from tests.clinic_time_test_support import REFERENCE_CLINIC_NOW_UTC
from tests.test_chat_receptionist_service import (
    create_chat_receptionist_service,
)
from tests.test_conversations import FakeConversationRepository
from tests.test_scheduling_services import (
    FakeAppointmentRepository,
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


def test_fake_repository_list_cancelable_for_patient_includes_scheduled_and_rescheduled() -> None:
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

    assert [appointment.id for appointment in appointments] == [scheduled.id, rescheduled.id]


def test_fake_repository_list_cancelable_for_patient_orders_by_start_time() -> None:
    patient_id = uuid4()
    doctor_id = uuid4()
    specialty_id = uuid4()
    later = create_appointment(
        patient_id=patient_id,
        doctor_id=doctor_id,
        specialty_id=specialty_id,
        start_time=datetime(2026, 7, 10, 18, 0, tzinfo=UTC),
        status=AppointmentStatus.RESCHEDULED,
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
