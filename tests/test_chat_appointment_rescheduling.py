from __future__ import annotations

from datetime import UTC, date, datetime
from unittest.mock import patch
from uuid import UUID, uuid4

import pytest

from app.domain.scheduling.enums import AppointmentStatus, AvailabilitySlotStatus
from app.models.scheduling import Appointment, Doctor, Patient
from app.services.appointment_holds import AppointmentSlotAlreadyHeldError
from app.services.appointment_rescheduling import AppointmentReschedulingService
from app.services.chat_appointment_cancellation import (
    APPOINTMENT_MANAGEMENT_AWAITING_APPOINTMENT_SELECTION,
    APPOINTMENT_MANAGEMENT_AWAITING_PATIENT_IDENTITY,
    APPOINTMENT_MANAGEMENT_MODE_CANCEL,
)
from app.services.chat_appointment_rescheduling import (
    APPOINTMENT_MANAGEMENT_AWAITING_NEW_SLOT_SELECTION,
    APPOINTMENT_MANAGEMENT_AWAITING_NEW_TIME_PREFERENCE,
    APPOINTMENT_MANAGEMENT_AWAITING_RESCHEDULE_CONFIRMATION,
    APPOINTMENT_MANAGEMENT_MODE_RESCHEDULE,
    RESCHEDULE_CONFIRMATION_REPROMPT_STUB,
    RESCHEDULE_HOLD_UNAVAILABLE_MESSAGE,
    RESCHEDULE_NEW_SLOT_SELECTION_AMBIGUOUS,
    RESCHEDULE_NEW_SLOT_SELECTION_REPROMPT,
    RESCHEDULE_NEW_TIME_PREFERENCE_REPROMPT,
    ChatAppointmentReschedulingOrchestrator,
    RescheduleSlotSelectionStatus,
)
from app.services.chat_receptionist import (
    _GENERIC_SCHEDULING_FALLBACK_MESSAGE,
    ChatMessageInput,
    ChatMessageResult,
    ChatReceptionistIntent,
    ChatReceptionistService,
    _resolve_contextual_fallback_reply,
)
from app.services.conversations import ConversationService
from app.services.date_parsing import FixedClock, NaturalLanguageDateParser
from app.services.post_cancellation_turn import (
    PostCancellationTurnDecision,
    classify_post_cancellation_turn,
)
from app.services.time_preferences import TimePreferenceParser
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
from tests.test_chat_receptionist_service import (
    FakeAppointmentHoldService,
    create_chat_receptionist_service,
)
from tests.test_conversations import FakeConversationRepository
from tests.test_scheduling_services import (
    FakeAppointmentRepository,
    create_availability_slot,
    create_demo_scheduling_service,
    create_service,
    create_specialty,
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


def _reach_reschedule_single_appointment_selection(
    service: ChatReceptionistService,
    *,
    appointments: list[Appointment],
    identity_message: str = "Felipe Godoy, 1996-09-19",
) -> tuple[ChatMessageResult, UUID]:
    result, conversation_id = _reach_reschedule_appointment_selection(
        service,
        appointments=appointments,
        identity_message=identity_message,
    )
    chat_context = result.conversation.conversation_metadata["chat_context"]
    assert len(chat_context["offered_appointments"]) == 1
    return result, conversation_id


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


def test_reschedule_single_appointment_yes_selects_and_advances() -> None:
    service, _repository, patient, _emily, reed = _create_chat_service_with_patient()
    selection_result, conversation_id = _reach_reschedule_single_appointment_selection(
        service,
        appointments=[
            _monday_cardiology_appointment(
                patient_id=patient.id,
                doctor_id=reed.id,
                specialty_id=reed.specialty_id,
            ),
        ],
    )

    with patch.object(
        AppointmentReschedulingService,
        "reschedule_appointment",
    ) as reschedule_appointment_mock:
        result = service.handle_message(
            ChatMessageInput(message="yes", conversation_id=conversation_id),
        )

    chat_context = result.conversation.conversation_metadata["chat_context"]
    offered = selection_result.conversation.conversation_metadata["chat_context"][
        "offered_appointments"
    ]
    assert result.intent == ChatReceptionistIntent.RESCHEDULE_REQUEST
    assert (
        chat_context["appointment_management_awaiting"]
        == APPOINTMENT_MANAGEMENT_AWAITING_NEW_TIME_PREFERENCE
    )
    assert chat_context["selected_appointment_id"] == offered[0]["appointment_id"]
    assert "Cardiology" in chat_context["selected_appointment_summary"]
    assert chat_context["selected_appointment_doctor_name"] == "Dr. Michael Reed"
    assert chat_context["selected_appointment_specialty_name"] == "Cardiology"
    assert chat_context.get("selected_appointment_start_time") is not None
    assert "what day or time would you prefer instead" in result.reply.lower()
    assert "rescheduled" not in result.reply.lower()
    assert "moved" not in result.reply.lower()
    assert chat_context_id_not_exposed(result.reply, chat_context=chat_context)
    reschedule_appointment_mock.assert_not_called()


def test_reschedule_single_appointment_no_does_not_select_or_execute() -> None:
    service, _repository, patient, _emily, reed = _create_chat_service_with_patient()
    _selection_result, conversation_id = _reach_reschedule_single_appointment_selection(
        service,
        appointments=[
            _monday_cardiology_appointment(
                patient_id=patient.id,
                doctor_id=reed.id,
                specialty_id=reed.specialty_id,
            ),
        ],
    )

    with patch.object(
        AppointmentReschedulingService,
        "reschedule_appointment",
    ) as reschedule_appointment_mock:
        result = service.handle_message(
            ChatMessageInput(message="no", conversation_id=conversation_id),
        )

    chat_context = result.conversation.conversation_metadata["chat_context"]
    assert result.intent == ChatReceptionistIntent.RESCHEDULE_REQUEST
    assert "won't reschedule" in result.reply.lower()
    assert chat_context.get("selected_appointment_id") is None
    assert chat_context.get("appointment_management_mode") is None
    reschedule_appointment_mock.assert_not_called()


def test_reschedule_selection_the_first_one_selects_first_appointment() -> None:
    service, _repository, patient, emily, reed = _create_chat_service_with_patient()
    selection_result, conversation_id = _reach_reschedule_appointment_selection(
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
        == APPOINTMENT_MANAGEMENT_AWAITING_NEW_TIME_PREFERENCE
    )
    assert chat_context["selected_appointment_id"] == offered[0]["appointment_id"]
    assert "Dermatology" in chat_context["selected_appointment_summary"]
    assert "what day or time would you prefer instead" in result.reply.lower()
    assert chat_context_id_not_exposed(result.reply, chat_context=chat_context)


def test_reschedule_selection_option_number_selects_first_appointment() -> None:
    service, _repository, patient, emily, reed = _create_chat_service_with_patient()
    _selection_result, conversation_id = _reach_reschedule_appointment_selection(
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
        == APPOINTMENT_MANAGEMENT_AWAITING_NEW_TIME_PREFERENCE
    )
    assert "Dermatology" in chat_context["selected_appointment_summary"]


def test_reschedule_selection_specialty_selects_matching_appointment() -> None:
    service, _repository, patient, emily, reed = _create_chat_service_with_patient()
    _selection_result, conversation_id = _reach_reschedule_appointment_selection(
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
        ChatMessageInput(message="the cardiology one", conversation_id=conversation_id),
    )

    chat_context = result.conversation.conversation_metadata["chat_context"]
    assert (
        chat_context["appointment_management_awaiting"]
        == APPOINTMENT_MANAGEMENT_AWAITING_NEW_TIME_PREFERENCE
    )
    assert "Cardiology" in chat_context["selected_appointment_summary"]
    assert "Dermatology" not in chat_context["selected_appointment_summary"]


def test_reschedule_selection_doctor_name_selects_matching_appointment() -> None:
    service, _repository, patient, emily, reed = _create_chat_service_with_patient()
    _selection_result, conversation_id = _reach_reschedule_appointment_selection(
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
        ChatMessageInput(message="Dr. Reed", conversation_id=conversation_id),
    )

    chat_context = result.conversation.conversation_metadata["chat_context"]
    assert (
        chat_context["appointment_management_awaiting"]
        == APPOINTMENT_MANAGEMENT_AWAITING_NEW_TIME_PREFERENCE
    )
    assert "Dr. Michael Reed" in chat_context["selected_appointment_summary"]


def test_reschedule_selection_weekday_selects_unique_appointment() -> None:
    service, _repository, patient, emily, reed = _create_chat_service_with_patient()
    _selection_result, conversation_id = _reach_reschedule_appointment_selection(
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
        == APPOINTMENT_MANAGEMENT_AWAITING_NEW_TIME_PREFERENCE
    )
    assert "Wednesday" in chat_context["selected_appointment_summary"]


def test_reschedule_selection_time_selects_unique_appointment() -> None:
    service, _repository, patient, emily, reed = _create_chat_service_with_patient()
    _selection_result, conversation_id = _reach_reschedule_appointment_selection(
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
        == APPOINTMENT_MANAGEMENT_AWAITING_NEW_TIME_PREFERENCE
    )
    assert "10:00" in chat_context["selected_appointment_summary"]


def test_reschedule_selection_success_preserves_patient_context() -> None:
    service, _repository, patient, emily, reed = _create_chat_service_with_patient()
    _selection_result, conversation_id = _reach_reschedule_appointment_selection(
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
    assert chat_context["resolved_patient_id"] == str(patient.id)
    assert chat_context["resolved_patient_name"] == "Felipe Godoy"
    assert chat_context.get("patient_resolution_id") is not None
    assert len(chat_context["offered_appointments"]) == 2
    assert chat_context.get("reschedule_offered_slots") is None
    assert chat_context.get("reschedule_selected_availability_slot_id") is None
    assert chat_context.get("reschedule_hold_id") is None


def test_reschedule_selection_zero_match_reprompts() -> None:
    service, _repository, patient, emily, reed = _create_chat_service_with_patient()
    _selection_result, conversation_id = _reach_reschedule_appointment_selection(
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
        ChatMessageInput(message="maybe tomorrow?", conversation_id=conversation_id),
    )

    chat_context = result.conversation.conversation_metadata["chat_context"]
    assert (
        chat_context["appointment_management_awaiting"]
        == APPOINTMENT_MANAGEMENT_AWAITING_APPOINTMENT_SELECTION
    )
    assert chat_context.get("selected_appointment_id") is None
    assert "Please choose one of the appointments I listed." in result.reply


def test_reschedule_selection_ambiguous_match_asks_clarification() -> None:
    service, _repository, patient, emily, reed = _create_chat_service_with_patient()
    monday_emily = _monday_cardiology_appointment(
        patient_id=patient.id,
        doctor_id=emily.id,
        specialty_id=emily.specialty_id,
    )
    monday_reed = _monday_cardiology_appointment(
        patient_id=patient.id,
        doctor_id=reed.id,
        specialty_id=reed.specialty_id,
    )
    _selection_result, conversation_id = _reach_reschedule_appointment_selection(
        service,
        appointments=[monday_emily, monday_reed],
    )

    result = service.handle_message(
        ChatMessageInput(message="Monday", conversation_id=conversation_id),
    )

    chat_context = result.conversation.conversation_metadata["chat_context"]
    assert (
        chat_context["appointment_management_awaiting"]
        == APPOINTMENT_MANAGEMENT_AWAITING_APPOINTMENT_SELECTION
    )
    assert chat_context.get("selected_appointment_id") is None
    assert "more than one matching appointment" in result.reply


def test_reschedule_selection_does_not_call_rescheduling_service() -> None:
    service, _repository, patient, emily, reed = _create_chat_service_with_patient()
    _selection_result, conversation_id = _reach_reschedule_appointment_selection(
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
        AppointmentReschedulingService,
        "reschedule_appointment",
    ) as reschedule_appointment_mock:
        service.handle_message(
            ChatMessageInput(message="1", conversation_id=conversation_id),
        )

    reschedule_appointment_mock.assert_not_called()


def _create_reschedule_service_with_wednesday_afternoon_availability() -> tuple[
    ChatReceptionistService,
    FakeConversationRepository,
    Doctor,
    Patient,
]:
    dermatology = create_specialty(name="Dermatology")
    emily = Doctor(
        id=uuid4(),
        specialty_id=dermatology.id,
        full_name="Dr. Emily Carter",
        email="emily.carter@example-clinic.test",
        phone_number="+1-555-0101",
        is_active=True,
    )
    patient = _felipe_patient()
    availability_slots = [
        create_availability_slot(
            doctor_id=emily.id,
            start_time=datetime(2026, 7, 1, 17, 0, tzinfo=UTC),
            status=AvailabilitySlotStatus.AVAILABLE,
        ),
        create_availability_slot(
            doctor_id=emily.id,
            start_time=datetime(2026, 7, 1, 18, 0, tzinfo=UTC),
            status=AvailabilitySlotStatus.AVAILABLE,
        ),
    ]
    scheduling = create_service(
        specialties=[dermatology],
        doctors=[emily],
        patients=[patient],
        availability_slots=availability_slots,
    )
    repository = FakeConversationRepository()
    conversations = ConversationService(repository=repository)
    service = create_chat_receptionist_service(
        conversations=conversations,
        scheduling=scheduling,
        hold_service=FakeAppointmentHoldService(),
        date_parser=NaturalLanguageDateParser(clock=FixedClock(current_date=date(2026, 7, 1))),
        time_preference_parser=TimePreferenceParser(),
    )
    return service, repository, emily, patient


_reschedule_wednesday_pm_service = _create_reschedule_service_with_wednesday_afternoon_availability


def _reach_reschedule_new_time_preference(
    service: ChatReceptionistService,
    *,
    appointments: list[Appointment],
) -> tuple[ChatMessageResult, UUID]:
    _selection_result, conversation_id = _reach_reschedule_single_appointment_selection(
        service,
        appointments=appointments,
    )
    result = service.handle_message(
        ChatMessageInput(message="yes", conversation_id=conversation_id),
    )
    chat_context = result.conversation.conversation_metadata["chat_context"]
    assert (
        chat_context["appointment_management_awaiting"]
        == APPOINTMENT_MANAGEMENT_AWAITING_NEW_TIME_PREFERENCE
    )
    return result, conversation_id


def _reach_reschedule_new_slot_selection(
    service: ChatReceptionistService,
    *,
    appointments: list[Appointment],
    preference_message: str = "Wednesday afternoon",
) -> tuple[ChatMessageResult, UUID]:
    _time_result, conversation_id = _reach_reschedule_new_time_preference(
        service,
        appointments=appointments,
    )
    result = service.handle_message(
        ChatMessageInput(message=preference_message, conversation_id=conversation_id),
    )
    chat_context = result.conversation.conversation_metadata["chat_context"]
    assert (
        chat_context["appointment_management_awaiting"]
        == APPOINTMENT_MANAGEMENT_AWAITING_NEW_SLOT_SELECTION
    )
    return result, conversation_id


def test_reschedule_wednesday_afternoon_checks_availability_and_lists_slots() -> None:
    service, _repository, emily, patient = _reschedule_wednesday_pm_service()
    _time_result, conversation_id = _reach_reschedule_new_time_preference(
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
        ChatMessageInput(message="Wednesday afternoon", conversation_id=conversation_id),
    )

    assert "I found these openings" in result.reply
    assert "Dr. Emily Carter" in result.reply
    assert "1." in result.reply
    assert "2." in result.reply
    assert "Which time would you like?" in result.reply


def test_reschedule_availability_advances_to_new_slot_selection() -> None:
    service, _repository, emily, patient = _reschedule_wednesday_pm_service()
    _time_result, conversation_id = _reach_reschedule_new_time_preference(
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
        ChatMessageInput(message="Wednesday afternoon", conversation_id=conversation_id),
    )

    chat_context = result.conversation.conversation_metadata["chat_context"]
    assert (
        chat_context["appointment_management_awaiting"]
        == APPOINTMENT_MANAGEMENT_AWAITING_NEW_SLOT_SELECTION
    )


def test_reschedule_availability_stores_offered_slots() -> None:
    service, _repository, emily, patient = _reschedule_wednesday_pm_service()
    _time_result, conversation_id = _reach_reschedule_new_time_preference(
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
        ChatMessageInput(message="Wednesday afternoon", conversation_id=conversation_id),
    )

    offered = result.conversation.conversation_metadata["chat_context"]["reschedule_offered_slots"]
    assert isinstance(offered, list)
    assert len(offered) == 2
    assert all("availability_slot_id" in slot for slot in offered)
    assert all("doctor_id" in slot for slot in offered)
    assert all("start_time" in slot for slot in offered)
    assert all("summary" in slot for slot in offered)


def test_reschedule_availability_reply_exposes_no_internal_ids() -> None:
    service, _repository, emily, patient = _reschedule_wednesday_pm_service()
    _time_result, conversation_id = _reach_reschedule_new_time_preference(
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
        ChatMessageInput(message="Wednesday afternoon", conversation_id=conversation_id),
    )

    chat_context = result.conversation.conversation_metadata["chat_context"]
    assert chat_context_id_not_exposed(result.reply, chat_context=chat_context)


def test_reschedule_availability_uses_selected_original_doctor() -> None:
    service, _repository, emily, patient = _reschedule_wednesday_pm_service()
    _time_result, conversation_id = _reach_reschedule_new_time_preference(
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
        ChatMessageInput(message="Wednesday afternoon", conversation_id=conversation_id),
    )

    offered = result.conversation.conversation_metadata["chat_context"]["reschedule_offered_slots"]
    assert all(slot["doctor_name"] == "Dr. Emily Carter" for slot in offered)
    assert all(slot["doctor_id"] == str(emily.id) for slot in offered)


def test_reschedule_time_window_filters_to_afternoon_slots() -> None:
    dermatology = create_specialty(name="Dermatology")
    emily = Doctor(
        id=uuid4(),
        specialty_id=dermatology.id,
        full_name="Dr. Emily Carter",
        email="emily.carter@example-clinic.test",
        phone_number="+1-555-0101",
        is_active=True,
    )
    patient = _felipe_patient()
    availability_slots = [
        create_availability_slot(
            doctor_id=emily.id,
            start_time=datetime(2026, 7, 2, 13, 0, tzinfo=UTC),
            status=AvailabilitySlotStatus.AVAILABLE,
        ),
        create_availability_slot(
            doctor_id=emily.id,
            start_time=datetime(2026, 7, 2, 17, 0, tzinfo=UTC),
            status=AvailabilitySlotStatus.AVAILABLE,
        ),
        create_availability_slot(
            doctor_id=emily.id,
            start_time=datetime(2026, 7, 2, 18, 0, tzinfo=UTC),
            status=AvailabilitySlotStatus.AVAILABLE,
        ),
    ]
    scheduling = create_service(
        specialties=[dermatology],
        doctors=[emily],
        patients=[patient],
        availability_slots=availability_slots,
    )
    repository = FakeConversationRepository()
    conversations = ConversationService(repository=repository)
    service = create_chat_receptionist_service(
        conversations=conversations,
        scheduling=scheduling,
        hold_service=FakeAppointmentHoldService(),
        date_parser=NaturalLanguageDateParser(clock=FixedClock(current_date=date(2026, 7, 1))),
        time_preference_parser=TimePreferenceParser(),
    )
    _time_result, conversation_id = _reach_reschedule_new_time_preference(
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
        ChatMessageInput(message="Thursday afternoon", conversation_id=conversation_id),
    )

    offered = result.conversation.conversation_metadata["chat_context"]["reschedule_offered_slots"]
    assert len(offered) == 2
    assert "09:00" not in result.reply
    assert "1." in result.reply
    assert "2." in result.reply


def test_reschedule_no_availability_keeps_new_time_preference() -> None:
    service, _repository, emily, patient = _reschedule_wednesday_pm_service()
    _time_result, conversation_id = _reach_reschedule_new_time_preference(
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
        ChatMessageInput(message="Wednesday morning", conversation_id=conversation_id),
    )

    chat_context = result.conversation.conversation_metadata["chat_context"]
    assert (
        chat_context["appointment_management_awaiting"]
        == APPOINTMENT_MANAGEMENT_AWAITING_NEW_TIME_PREFERENCE
    )
    assert "don't see openings" in result.reply.lower()


def test_reschedule_no_availability_clears_stale_offered_slots() -> None:
    service, _repository, emily, patient = _reschedule_wednesday_pm_service()
    _time_result, conversation_id = _reach_reschedule_new_time_preference(
        service,
        appointments=[
            _wednesday_appointment(
                patient_id=patient.id,
                doctor_id=emily.id,
                specialty_id=emily.specialty_id,
            ),
        ],
    )

    service.conversations.merge_chat_context(
        conversation_id=conversation_id,
        chat_context={
            "reschedule_offered_slots": [{"availability_slot_id": "stale", "summary": "stale"}],
        },
    )

    result = service.handle_message(
        ChatMessageInput(message="Wednesday morning", conversation_id=conversation_id),
    )

    chat_context = result.conversation.conversation_metadata["chat_context"]
    assert chat_context.get("reschedule_offered_slots") is None
    assert (
        chat_context["appointment_management_awaiting"]
        == APPOINTMENT_MANAGEMENT_AWAITING_NEW_TIME_PREFERENCE
    )


def test_reschedule_unclear_preference_reprompts_for_day_or_time() -> None:
    service, _repository, emily, patient = _reschedule_wednesday_pm_service()
    _time_result, conversation_id = _reach_reschedule_new_time_preference(
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
        ChatMessageInput(message="maybe?", conversation_id=conversation_id),
    )

    assert result.reply == RESCHEDULE_NEW_TIME_PREFERENCE_REPROMPT
    chat_context = result.conversation.conversation_metadata["chat_context"]
    assert (
        chat_context["appointment_management_awaiting"]
        == APPOINTMENT_MANAGEMENT_AWAITING_NEW_TIME_PREFERENCE
    )
    assert result.reply != _GENERIC_SCHEDULING_FALLBACK_MESSAGE


def test_reschedule_new_preference_clears_stale_slot_and_hold_context() -> None:
    service, _repository, emily, patient = _reschedule_wednesday_pm_service()
    _time_result, conversation_id = _reach_reschedule_new_time_preference(
        service,
        appointments=[
            _wednesday_appointment(
                patient_id=patient.id,
                doctor_id=emily.id,
                specialty_id=emily.specialty_id,
            ),
        ],
    )

    service.conversations.merge_chat_context(
        conversation_id=conversation_id,
        chat_context={
            "reschedule_offered_slots": [{"availability_slot_id": "stale"}],
            "reschedule_selected_availability_slot_id": "stale-slot",
            "reschedule_selected_start_time": "2026-07-01T16:00:00+00:00",
            "reschedule_selected_doctor_id": str(emily.id),
            "reschedule_selected_doctor_name": "Dr. Emily Carter",
            "reschedule_hold_id": "stale-hold",
            "reschedule_hold_expires_at": "2026-07-01T16:05:00+00:00",
            "reschedule_hold_owner_id": "stale-owner",
        },
    )

    result = service.handle_message(
        ChatMessageInput(message="Wednesday afternoon", conversation_id=conversation_id),
    )

    chat_context = result.conversation.conversation_metadata["chat_context"]
    assert chat_context.get("reschedule_selected_availability_slot_id") is None
    assert chat_context.get("reschedule_hold_id") is None
    assert chat_context.get("reschedule_hold_expires_at") is None
    assert chat_context.get("reschedule_hold_owner_id") is None
    assert chat_context.get("reschedule_selected_start_time") is None
    assert chat_context.get("reschedule_selected_doctor_id") is None
    assert len(chat_context["reschedule_offered_slots"]) == 2


def test_reschedule_first_offered_slot_creates_hold_and_requests_confirmation() -> None:
    service, _repository, emily, patient = _reschedule_wednesday_pm_service()
    hold_service = service.appointment_holds
    assert isinstance(hold_service, FakeAppointmentHoldService)
    _availability_result, conversation_id = _reach_reschedule_new_slot_selection(
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
        AppointmentReschedulingService,
        "reschedule_appointment",
    ) as reschedule_appointment_mock:
        result = service.handle_message(
            ChatMessageInput(message="the first one", conversation_id=conversation_id),
        )

    reschedule_appointment_mock.assert_not_called()
    assert "Please confirm" in result.reply
    assert "reschedule your" in result.reply
    assert "rescheduled" not in result.reply.lower()
    assert "moved" not in result.reply.lower()
    assert "done" not in result.reply.lower()
    assert "changed" not in result.reply.lower()

    chat_context = result.conversation.conversation_metadata["chat_context"]
    assert (
        chat_context["appointment_management_awaiting"]
        == APPOINTMENT_MANAGEMENT_AWAITING_RESCHEDULE_CONFIRMATION
    )
    assert chat_context.get("reschedule_selected_availability_slot_id")
    assert chat_context.get("reschedule_selected_start_time")
    assert chat_context.get("reschedule_selected_doctor_id") == str(emily.id)
    assert chat_context.get("reschedule_selected_doctor_name") == "Dr. Emily Carter"
    assert chat_context.get("reschedule_hold_id")
    assert chat_context.get("reschedule_hold_expires_at")
    assert chat_context.get("reschedule_hold_owner_id") == str(result.conversation.id)
    assert chat_context.get("selected_appointment_id")
    assert len(chat_context.get("reschedule_offered_slots") or []) == 2
    assert len(hold_service.create_hold_calls) == 1
    assert hold_service.create_hold_calls[0]["owner_id"] == str(result.conversation.id)
    assert chat_context_id_not_exposed(result.reply, chat_context=chat_context)


@pytest.mark.parametrize("message", ["1", "number 1"])
def test_reschedule_option_number_selects_first_slot_and_creates_hold(message: str) -> None:
    service, _repository, emily, patient = _reschedule_wednesday_pm_service()
    hold_service = service.appointment_holds
    assert isinstance(hold_service, FakeAppointmentHoldService)
    _availability_result, conversation_id = _reach_reschedule_new_slot_selection(
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

    chat_context = result.conversation.conversation_metadata["chat_context"]
    assert (
        chat_context["appointment_management_awaiting"]
        == APPOINTMENT_MANAGEMENT_AWAITING_RESCHEDULE_CONFIRMATION
    )
    assert chat_context.get("reschedule_hold_id")
    assert len(hold_service.create_hold_calls) == 1


def test_reschedule_unique_time_match_selects_slot_and_creates_hold() -> None:
    dermatology = create_specialty(name="Dermatology")
    emily = Doctor(
        id=uuid4(),
        specialty_id=dermatology.id,
        full_name="Dr. Emily Carter",
        email="emily.carter@example-clinic.test",
        phone_number="+1-555-0101",
        is_active=True,
    )
    patient = _felipe_patient()
    afternoon_slot = create_availability_slot(
        doctor_id=emily.id,
        start_time=datetime(2026, 7, 1, 18, 0, tzinfo=UTC),
        status=AvailabilitySlotStatus.AVAILABLE,
    )
    scheduling = create_service(
        specialties=[dermatology],
        doctors=[emily],
        patients=[patient],
        availability_slots=[afternoon_slot],
    )
    repository = FakeConversationRepository()
    conversations = ConversationService(repository=repository)
    service = create_chat_receptionist_service(
        conversations=conversations,
        scheduling=scheduling,
        hold_service=FakeAppointmentHoldService(),
        date_parser=NaturalLanguageDateParser(clock=FixedClock(current_date=date(2026, 7, 1))),
        time_preference_parser=TimePreferenceParser(),
    )
    _availability_result, conversation_id = _reach_reschedule_new_slot_selection(
        service,
        appointments=[
            _wednesday_appointment(
                patient_id=patient.id,
                doctor_id=emily.id,
                specialty_id=emily.specialty_id,
            ),
        ],
        preference_message="Wednesday",
    )

    result = service.handle_message(
        ChatMessageInput(message="2 PM", conversation_id=conversation_id),
    )

    chat_context = result.conversation.conversation_metadata["chat_context"]
    assert (
        chat_context["appointment_management_awaiting"]
        == APPOINTMENT_MANAGEMENT_AWAITING_RESCHEDULE_CONFIRMATION
    )
    assert chat_context.get("reschedule_hold_id")


def test_reschedule_ambiguous_time_match_does_not_create_hold() -> None:
    service, _repository, emily, patient = _reschedule_wednesday_pm_service()
    hold_service = service.appointment_holds
    assert isinstance(hold_service, FakeAppointmentHoldService)
    _availability_result, conversation_id = _reach_reschedule_new_slot_selection(
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
        ChatMessageInput(message="Wednesday", conversation_id=conversation_id),
    )

    assert result.reply == RESCHEDULE_NEW_SLOT_SELECTION_AMBIGUOUS
    chat_context = result.conversation.conversation_metadata["chat_context"]
    assert (
        chat_context["appointment_management_awaiting"]
        == APPOINTMENT_MANAGEMENT_AWAITING_NEW_SLOT_SELECTION
    )
    assert chat_context.get("reschedule_hold_id") is None
    assert hold_service.create_hold_calls == []


def test_reschedule_zero_slot_match_reprompts_without_hold() -> None:
    service, _repository, emily, patient = _reschedule_wednesday_pm_service()
    hold_service = service.appointment_holds
    assert isinstance(hold_service, FakeAppointmentHoldService)
    _availability_result, conversation_id = _reach_reschedule_new_slot_selection(
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
        ChatMessageInput(message="maybe?", conversation_id=conversation_id),
    )

    assert result.reply == RESCHEDULE_NEW_SLOT_SELECTION_REPROMPT
    chat_context = result.conversation.conversation_metadata["chat_context"]
    assert (
        chat_context["appointment_management_awaiting"]
        == APPOINTMENT_MANAGEMENT_AWAITING_NEW_SLOT_SELECTION
    )
    assert chat_context.get("reschedule_hold_id") is None
    assert hold_service.create_hold_calls == []


def test_reschedule_confirmation_prompt_includes_original_and_new_slot_summaries() -> None:
    service, _repository, emily, patient = _reschedule_wednesday_pm_service()
    _availability_result, conversation_id = _reach_reschedule_new_slot_selection(
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
        ChatMessageInput(message="1", conversation_id=conversation_id),
    )

    assert "Dermatology appointment with Dr. Emily Carter" in result.reply
    assert "Wednesday at 13:00" in result.reply


def test_reschedule_hold_failure_returns_safe_message_and_stays_in_slot_selection() -> None:
    dermatology = create_specialty(name="Dermatology")
    emily = Doctor(
        id=uuid4(),
        specialty_id=dermatology.id,
        full_name="Dr. Emily Carter",
        email="emily.carter@example-clinic.test",
        phone_number="+1-555-0101",
        is_active=True,
    )
    patient = _felipe_patient()
    availability_slots = [
        create_availability_slot(
            doctor_id=emily.id,
            start_time=datetime(2026, 7, 1, 17, 0, tzinfo=UTC),
            status=AvailabilitySlotStatus.AVAILABLE,
        ),
    ]
    scheduling = create_service(
        specialties=[dermatology],
        doctors=[emily],
        patients=[patient],
        availability_slots=availability_slots,
    )
    repository = FakeConversationRepository()
    conversations = ConversationService(repository=repository)
    hold_service = FakeAppointmentHoldService(
        create_hold_error=AppointmentSlotAlreadyHeldError("slot already has an active hold"),
    )
    service = create_chat_receptionist_service(
        conversations=conversations,
        scheduling=scheduling,
        hold_service=hold_service,
        date_parser=NaturalLanguageDateParser(clock=FixedClock(current_date=date(2026, 7, 1))),
        time_preference_parser=TimePreferenceParser(),
    )
    _availability_result, conversation_id = _reach_reschedule_new_slot_selection(
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
        AppointmentReschedulingService,
        "reschedule_appointment",
    ) as reschedule_appointment_mock:
        result = service.handle_message(
            ChatMessageInput(message="1", conversation_id=conversation_id),
        )

    reschedule_appointment_mock.assert_not_called()
    assert result.reply == RESCHEDULE_HOLD_UNAVAILABLE_MESSAGE
    chat_context = result.conversation.conversation_metadata["chat_context"]
    assert (
        chat_context["appointment_management_awaiting"]
        == APPOINTMENT_MANAGEMENT_AWAITING_NEW_SLOT_SELECTION
    )
    assert chat_context.get("selected_appointment_id")
    assert chat_context.get("reschedule_hold_id") is None


def test_reschedule_confirmation_awaiting_does_not_execute_reschedule() -> None:
    service, _repository, emily, patient = _reschedule_wednesday_pm_service()
    _availability_result, conversation_id = _reach_reschedule_new_slot_selection(
        service,
        appointments=[
            _wednesday_appointment(
                patient_id=patient.id,
                doctor_id=emily.id,
                specialty_id=emily.specialty_id,
            ),
        ],
    )
    service.handle_message(
        ChatMessageInput(message="1", conversation_id=conversation_id),
    )

    with patch.object(
        AppointmentReschedulingService,
        "reschedule_appointment",
    ) as reschedule_appointment_mock:
        result = service.handle_message(
            ChatMessageInput(message="yes, please reschedule it", conversation_id=conversation_id),
        )

    reschedule_appointment_mock.assert_not_called()
    assert result.reply == RESCHEDULE_CONFIRMATION_REPROMPT_STUB
    chat_context = result.conversation.conversation_metadata["chat_context"]
    assert (
        chat_context["appointment_management_awaiting"]
        == APPOINTMENT_MANAGEMENT_AWAITING_RESCHEDULE_CONFIRMATION
    )


def test_reschedule_slot_selection_does_not_call_rescheduling_service() -> None:
    service, _repository, emily, patient = _reschedule_wednesday_pm_service()
    _availability_result, conversation_id = _reach_reschedule_new_slot_selection(
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
        AppointmentReschedulingService,
        "reschedule_appointment",
    ) as reschedule_appointment_mock:
        service.handle_message(
            ChatMessageInput(message="the first one", conversation_id=conversation_id),
        )

    reschedule_appointment_mock.assert_not_called()


def test_resolve_reschedule_slot_selection_first_option() -> None:
    service, _repository, emily, patient = _reschedule_wednesday_pm_service()
    orchestrator = service._appointment_rescheduling
    assert isinstance(orchestrator, ChatAppointmentReschedulingOrchestrator)
    _availability_result, conversation_id = _reach_reschedule_new_slot_selection(
        service,
        appointments=[
            _wednesday_appointment(
                patient_id=patient.id,
                doctor_id=emily.id,
                specialty_id=emily.specialty_id,
            ),
        ],
    )
    chat_context = _availability_result.conversation.conversation_metadata["chat_context"]

    selection = orchestrator.resolve_reschedule_slot_selection(
        message="the first one",
        chat_context=chat_context,
    )

    assert selection.status is RescheduleSlotSelectionStatus.UNIQUE
    assert selection.selected_slot is not None


def test_reschedule_time_preference_does_not_call_rescheduling_service() -> None:
    service, _repository, emily, patient = _reschedule_wednesday_pm_service()
    _time_result, conversation_id = _reach_reschedule_new_time_preference(
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
        AppointmentReschedulingService,
        "reschedule_appointment",
    ) as reschedule_appointment_mock:
        service.handle_message(
            ChatMessageInput(message="Wednesday afternoon", conversation_id=conversation_id),
        )

    reschedule_appointment_mock.assert_not_called()


def test_reschedule_time_preference_does_not_call_hold_service() -> None:
    service, _repository, emily, patient = _reschedule_wednesday_pm_service()
    hold_service = service.appointment_holds
    assert isinstance(hold_service, FakeAppointmentHoldService)
    _time_result, conversation_id = _reach_reschedule_new_time_preference(
        service,
        appointments=[
            _wednesday_appointment(
                patient_id=patient.id,
                doctor_id=emily.id,
                specialty_id=emily.specialty_id,
            ),
        ],
    )

    service.handle_message(
        ChatMessageInput(message="Wednesday afternoon", conversation_id=conversation_id),
    )

    assert hold_service.create_hold_calls == []


def test_resolve_contextual_fallback_for_reschedule_new_slot_selection() -> None:
    resolved = _resolve_contextual_fallback_reply(
        {
            "appointment_management_mode": APPOINTMENT_MANAGEMENT_MODE_RESCHEDULE,
            "appointment_management_awaiting": APPOINTMENT_MANAGEMENT_AWAITING_NEW_SLOT_SELECTION,
        },
    )

    assert resolved is not None
    intent, content = resolved
    assert intent == ChatReceptionistIntent.RESCHEDULE_REQUEST
    assert content == RESCHEDULE_NEW_SLOT_SELECTION_REPROMPT


def test_resolve_contextual_fallback_for_reschedule_confirmation() -> None:
    resolved = _resolve_contextual_fallback_reply(
        {
            "appointment_management_mode": APPOINTMENT_MANAGEMENT_MODE_RESCHEDULE,
            "appointment_management_awaiting": (
                APPOINTMENT_MANAGEMENT_AWAITING_RESCHEDULE_CONFIRMATION
            ),
        },
    )

    assert resolved is not None
    intent, content = resolved
    assert intent == ChatReceptionistIntent.RESCHEDULE_REQUEST
    assert content == RESCHEDULE_CONFIRMATION_REPROMPT_STUB
