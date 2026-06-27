from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import patch
from uuid import UUID

import pytest

from app.domain.scheduling.enums import AppointmentStatus
from app.models.scheduling import Appointment
from app.services.appointment_rescheduling import AppointmentReschedulingService
from app.services.chat_appointment_cancellation import (
    APPOINTMENT_MANAGEMENT_AWAITING_APPOINTMENT_SELECTION,
    APPOINTMENT_MANAGEMENT_AWAITING_PATIENT_IDENTITY,
    APPOINTMENT_MANAGEMENT_MODE_CANCEL,
)
from app.services.chat_appointment_rescheduling import APPOINTMENT_MANAGEMENT_MODE_RESCHEDULE
from app.services.chat_receptionist import (
    _GENERIC_SCHEDULING_FALLBACK_MESSAGE,
    ChatMessageInput,
    ChatMessageResult,
    ChatReceptionistIntent,
    ChatReceptionistService,
    _resolve_contextual_fallback_reply,
)
from app.services.conversations import ConversationService
from app.services.post_cancellation_turn import (
    PostCancellationTurnDecision,
    classify_post_cancellation_turn,
)
from tests.clinic_time_test_support import REFERENCE_CLINIC_NOW_UTC
from tests.test_chat_appointment_cancellation import (
    _add_appointments,
    _complete_cancellation,
    _create_chat_service_with_patient,
    _felipe_patient,
    _friday_appointment,
    _wednesday_appointment,
    chat_context_id_not_exposed,
)
from tests.test_chat_booking_identity_orchestration import (
    _book_appointment_and_get_conversation,
    _create_service,
)
from tests.test_chat_receptionist_service import create_chat_receptionist_service
from tests.test_conversations import FakeConversationRepository
from tests.test_scheduling_services import (
    FakeAppointmentRepository,
    create_demo_scheduling_service,
)


def _monday_cardiology_appointment(
    *,
    patient_id: UUID,
    doctor_id: UUID,
    specialty_id: UUID,
    status: AppointmentStatus = AppointmentStatus.SCHEDULED,
) -> Appointment:
    return Appointment(
        patient_id=patient_id,
        doctor_id=doctor_id,
        specialty_id=specialty_id,
        start_time=datetime(2026, 7, 6, 14, 0, tzinfo=UTC),
        end_time=datetime(2026, 7, 6, 14, 30, tzinfo=UTC),
        status=status,
        reason="Cardiology follow-up",
    )


def _reach_reschedule_appointment_selection(
    service: ChatReceptionistService,
    *,
    appointments: list[Appointment],
    identity_message: str = "Felipe Godoy, 1996-09-19",
) -> tuple[ChatMessageResult, UUID]:
    _add_appointments(service, appointments)
    started = service.handle_message(
        ChatMessageInput(message="I need to reschedule my appointment"),
    )
    result = service.handle_message(
        ChatMessageInput(
            message=identity_message,
            conversation_id=started.conversation.id,
        ),
    )
    return result, started.conversation.id


@pytest.fixture()
def scheduling_chat_service() -> tuple[ChatReceptionistService, FakeConversationRepository]:
    repository = FakeConversationRepository()
    conversations = ConversationService(repository=repository)
    scheduling = create_demo_scheduling_service()
    service = create_chat_receptionist_service(
        conversations=conversations,
        scheduling=scheduling,
    )
    return service, repository


@pytest.mark.parametrize(
    "message",
    [
        "I need to reschedule my appointment",
        "I want to reschedule",
        "Can I move my appointment?",
        "move appointment",
    ],
)
def test_reschedule_request_enters_reschedule_task_frame(
    scheduling_chat_service: tuple[ChatReceptionistService, object],
    message: str,
) -> None:
    service, _repository = scheduling_chat_service

    result = service.handle_message(ChatMessageInput(message=message))

    assert result.intent == ChatReceptionistIntent.RESCHEDULE_REQUEST
    reply = result.reply.lower()
    assert "full name" in reply
    assert "date of birth" in reply
    assert "preferred new time" not in reply
    assert "which appointment you want to move" not in reply
    chat_context = result.conversation.conversation_metadata["chat_context"]
    assert chat_context["appointment_management_mode"] == APPOINTMENT_MANAGEMENT_MODE_RESCHEDULE
    assert (
        chat_context["appointment_management_awaiting"]
        == APPOINTMENT_MANAGEMENT_AWAITING_PATIENT_IDENTITY
    )


def test_unclear_message_during_reschedule_identity_intake_reprompts(
    scheduling_chat_service: tuple[ChatReceptionistService, object],
) -> None:
    service, _repository = scheduling_chat_service

    first = service.handle_message(
        ChatMessageInput(message="I need to reschedule my appointment"),
    )
    result = service.handle_message(
        ChatMessageInput(message="???", conversation_id=first.conversation.id),
    )

    assert result.intent == ChatReceptionistIntent.RESCHEDULE_REQUEST
    assert "full name" in result.reply.lower()
    assert "date of birth" in result.reply.lower()
    assert result.reply != _GENERIC_SCHEDULING_FALLBACK_MESSAGE
    chat_context = result.conversation.conversation_metadata["chat_context"]
    assert chat_context["appointment_management_mode"] == APPOINTMENT_MANAGEMENT_MODE_RESCHEDULE
    assert (
        chat_context["appointment_management_awaiting"]
        == APPOINTMENT_MANAGEMENT_AWAITING_PATIENT_IDENTITY
    )


def test_resolve_contextual_fallback_for_reschedule_patient_identity() -> None:
    resolved = _resolve_contextual_fallback_reply(
        {
            "appointment_management_mode": APPOINTMENT_MANAGEMENT_MODE_RESCHEDULE,
            "appointment_management_awaiting": APPOINTMENT_MANAGEMENT_AWAITING_PATIENT_IDENTITY,
        },
    )

    assert resolved is not None
    intent, content = resolved
    assert intent == ChatReceptionistIntent.RESCHEDULE_REQUEST
    assert "full name" in content.lower()
    assert "date of birth" in content.lower()


def test_reschedule_task_frame_does_not_call_rescheduling_service(
    scheduling_chat_service: tuple[ChatReceptionistService, object],
) -> None:
    service, _repository = scheduling_chat_service

    with patch.object(
        AppointmentReschedulingService,
        "reschedule_appointment",
    ) as reschedule_appointment_mock:
        result = service.handle_message(
            ChatMessageInput(message="I need to reschedule my appointment"),
        )

    assert result.intent == ChatReceptionistIntent.RESCHEDULE_REQUEST
    reschedule_appointment_mock.assert_not_called()


def test_post_booking_reschedule_request_enters_reschedule_task_frame() -> None:
    service, tracking_booking, _scheduling = _create_service(mixed_slots=True)
    conversation = _book_appointment_and_get_conversation(service)

    result = service.handle_message(
        ChatMessageInput(
            message="I need to reschedule",
            conversation_id=conversation.id,
        ),
    )

    reply = result.reply.lower()
    assert result.intent == ChatReceptionistIntent.RESCHEDULE_REQUEST
    assert "full name" in reply
    assert "date of birth" in reply
    assert "already confirmed" not in reply
    assert "preferred new time" not in reply
    assert len(tracking_booking.book_calls) == 1
    context = result.conversation.conversation_metadata["chat_context"]
    assert context["appointment_management_mode"] == APPOINTMENT_MANAGEMENT_MODE_RESCHEDULE
    assert (
        context["appointment_management_awaiting"]
        == APPOINTMENT_MANAGEMENT_AWAITING_PATIENT_IDENTITY
    )


def test_post_cancellation_reschedule_request_enters_reschedule_task_frame() -> None:
    message = "I need to reschedule"
    assert (
        classify_post_cancellation_turn(message=message).decision
        is PostCancellationTurnDecision.RESCHEDULE_REQUEST
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

    assert result.intent == ChatReceptionistIntent.RESCHEDULE_REQUEST
    assert "full name" in result.reply.lower()
    assert "date of birth" in result.reply.lower()
    context = result.conversation.conversation_metadata["chat_context"]
    assert context["appointment_management_mode"] == APPOINTMENT_MANAGEMENT_MODE_RESCHEDULE
    assert (
        context["appointment_management_awaiting"]
        == APPOINTMENT_MANAGEMENT_AWAITING_PATIENT_IDENTITY
    )


def test_cancellation_routing_unchanged_when_not_rescheduling(
    scheduling_chat_service: tuple[ChatReceptionistService, object],
) -> None:
    service, _repository = scheduling_chat_service

    result = service.handle_message(
        ChatMessageInput(message="I want to cancel my appointment"),
    )

    assert result.intent == ChatReceptionistIntent.CANCEL_REQUEST
    chat_context = result.conversation.conversation_metadata["chat_context"]
    assert chat_context["appointment_management_mode"] == APPOINTMENT_MANAGEMENT_MODE_CANCEL
    assert (
        chat_context["appointment_management_awaiting"]
        == APPOINTMENT_MANAGEMENT_AWAITING_PATIENT_IDENTITY
    )


def test_reschedule_identity_resolves_patient_and_lists_one_appointment() -> None:
    service, _repository, patient, _emily, reed = _create_chat_service_with_patient()
    _add_appointments(
        service,
        [
            _monday_cardiology_appointment(
                patient_id=patient.id,
                doctor_id=reed.id,
                specialty_id=reed.specialty_id,
            ),
        ],
    )

    started = service.handle_message(
        ChatMessageInput(message="I need to reschedule my appointment"),
    )
    result = service.handle_message(
        ChatMessageInput(
            message="Felipe Godoy, 1996-09-19",
            conversation_id=started.conversation.id,
        ),
    )

    chat_context = result.conversation.conversation_metadata["chat_context"]
    assert result.intent == ChatReceptionistIntent.RESCHEDULE_REQUEST
    assert chat_context["appointment_management_mode"] == APPOINTMENT_MANAGEMENT_MODE_RESCHEDULE
    assert (
        chat_context["appointment_management_awaiting"]
        == APPOINTMENT_MANAGEMENT_AWAITING_APPOINTMENT_SELECTION
    )
    assert chat_context["resolved_patient_id"] == str(patient.id)
    assert chat_context["resolved_patient_name"] == "Felipe Godoy"
    assert chat_context.get("selected_appointment_id") is None
    offered = chat_context["offered_appointments"]
    assert len(offered) == 1
    assert "appointment_id" in offered[0]
    assert "summary" in offered[0]


def test_reschedule_single_appointment_reply_asks_for_confirmation() -> None:
    service, _repository, patient, _emily, reed = _create_chat_service_with_patient()
    _add_appointments(
        service,
        [
            _monday_cardiology_appointment(
                patient_id=patient.id,
                doctor_id=reed.id,
                specialty_id=reed.specialty_id,
            ),
        ],
    )

    started = service.handle_message(ChatMessageInput(message="I need to reschedule"))
    result = service.handle_message(
        ChatMessageInput(
            message="Felipe Godoy, 1996-09-19",
            conversation_id=started.conversation.id,
        ),
    )

    reply = result.reply
    assert "Cardiology appointment with Dr. Michael Reed" in reply
    assert "Monday" in reply
    assert "10:00" in reply
    assert "appointment you want to reschedule" in reply
    assert str(patient.id) not in reply
    chat_context = result.conversation.conversation_metadata["chat_context"]
    assert chat_context_id_not_exposed(reply, chat_context=chat_context)


@pytest.mark.parametrize(
    "identity_message",
    [
        "Felipe Godoy, September 19, 1996",
        "My name is Felipe Godoy and my date of birth is September 19th, 1996",
    ],
)
def test_reschedule_identity_accepts_natural_language_formats(identity_message: str) -> None:
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

    started = service.handle_message(ChatMessageInput(message="I need to reschedule"))
    result = service.handle_message(
        ChatMessageInput(message=identity_message, conversation_id=started.conversation.id),
    )

    chat_context = result.conversation.conversation_metadata["chat_context"]
    assert result.intent == ChatReceptionistIntent.RESCHEDULE_REQUEST
    assert (
        chat_context["appointment_management_awaiting"]
        == APPOINTMENT_MANAGEMENT_AWAITING_APPOINTMENT_SELECTION
    )
    assert chat_context["resolved_patient_id"] == str(patient.id)


def test_reschedule_multiple_appointments_sets_selection_context() -> None:
    service, _repository, patient, emily, reed = _create_chat_service_with_patient()
    result, _conversation_id = _reach_reschedule_appointment_selection(
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
    assert "Which one would you like to reschedule?" in result.reply
    assert chat_context_id_not_exposed(result.reply, chat_context=chat_context)


def test_reschedule_no_appointments_returns_guidance_and_keeps_identity_awaiting() -> None:
    service, _repository, _patient, _emily, _reed = _create_chat_service_with_patient()

    started = service.handle_message(ChatMessageInput(message="I need to reschedule"))
    result = service.handle_message(
        ChatMessageInput(
            message="Felipe Godoy, 1996-09-19",
            conversation_id=started.conversation.id,
        ),
    )

    chat_context = result.conversation.conversation_metadata["chat_context"]
    assert "not seeing any upcoming appointments that can be rescheduled" in result.reply.lower()
    assert chat_context["appointment_management_mode"] == APPOINTMENT_MANAGEMENT_MODE_RESCHEDULE
    assert (
        chat_context["appointment_management_awaiting"]
        == APPOINTMENT_MANAGEMENT_AWAITING_PATIENT_IDENTITY
    )
    assert chat_context.get("selected_appointment_id") is None
    assert chat_context.get("offered_appointments") is None


def test_reschedule_incomplete_identity_reprompts() -> None:
    service, _repository, _patient, _emily, _reed = _create_chat_service_with_patient()

    started = service.handle_message(ChatMessageInput(message="I need to reschedule"))
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


def test_reschedule_patient_not_found_does_not_list_appointments() -> None:
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

    started = service.handle_message(ChatMessageInput(message="I need to reschedule"))
    with patch.object(
        AppointmentReschedulingService,
        "reschedule_appointment",
    ) as reschedule_appointment_mock:
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
    reschedule_appointment_mock.assert_not_called()


def test_fake_repository_list_reschedulable_for_patient_includes_scheduled_only() -> None:
    patient = _felipe_patient()
    scheduled = _wednesday_appointment(
        patient_id=patient.id,
        doctor_id=patient.id,
        specialty_id=patient.id,
    )
    rescheduled = _friday_appointment(
        patient_id=patient.id,
        doctor_id=patient.id,
        specialty_id=patient.id,
        status=AppointmentStatus.RESCHEDULED,
    )
    cancelled = _friday_appointment(
        patient_id=patient.id,
        doctor_id=patient.id,
        specialty_id=patient.id,
        status=AppointmentStatus.CANCELLED,
    )
    repository = FakeAppointmentRepository([scheduled, rescheduled, cancelled])

    appointments = repository.list_reschedulable_for_patient(
        patient_id=patient.id,
        start_from=REFERENCE_CLINIC_NOW_UTC,
    )

    assert appointments == [scheduled]


def test_fake_repository_list_reschedulable_for_patient_orders_by_start_time() -> None:
    patient = _felipe_patient()
    later = _friday_appointment(
        patient_id=patient.id,
        doctor_id=patient.id,
        specialty_id=patient.id,
    )
    earlier = _wednesday_appointment(
        patient_id=patient.id,
        doctor_id=patient.id,
        specialty_id=patient.id,
    )
    repository = FakeAppointmentRepository([later, earlier])

    appointments = repository.list_reschedulable_for_patient(
        patient_id=patient.id,
        start_from=REFERENCE_CLINIC_NOW_UTC,
    )

    assert [appointment.id for appointment in appointments] == [earlier.id, later.id]
