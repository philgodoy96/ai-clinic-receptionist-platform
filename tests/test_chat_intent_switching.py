from __future__ import annotations

from datetime import UTC, datetime

import pytest

from app.domain.scheduling.enums import AppointmentStatus
from app.models.scheduling import Appointment
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
)
from app.services.chat_booking_identity import ParsedPatientFields
from app.services.chat_intent_switching import (
    PENDING_INTENT_SWITCH_KEY,
    IntentSwitchTarget,
    build_intent_switch_confirmation_message,
    detect_intent_override,
    is_expected_in_flow_input,
)
from app.services.chat_receptionist import (
    ChatMessageInput,
    ChatReceptionistIntent,
    ChatReceptionistService,
)
from app.services.conversation_health import ConversationHealthService
from app.services.conversations import ConversationService
from app.services.human_escalations import HumanEscalationService
from tests.test_chat_appointment_cancellation import (
    _add_appointments,
    _create_chat_service_with_patient,
    _friday_appointment,
    _wednesday_appointment,
)
from tests.test_chat_appointment_rescheduling import (
    _reach_reschedule_appointment_selection,
    _reach_reschedule_new_slot_selection,
    _reschedule_wednesday_pm_service,
)
from tests.test_chat_receptionist_service import create_chat_receptionist_service
from tests.test_conversations import FakeConversationRepository
from tests.test_human_escalations import FakeHumanEscalationRepository
from tests.test_scheduling_services import create_demo_scheduling_service


def _parse_patient_fields(message: str) -> ParsedPatientFields:
    from tests.test_chat_receptionist_service import create_chat_receptionist_service as _create

    service = _create(
        conversations=ConversationService(repository=FakeConversationRepository()),
        scheduling=create_demo_scheduling_service(),
    )
    parsed = service.parse_patient_identity(message, booking_context=False)
    return ParsedPatientFields(
        full_name=parsed.full_name,
        date_of_birth=parsed.date_of_birth,
        email=parsed.email,
        phone=parsed.phone,
    )


def test_detect_clear_booking_switch_from_reschedule_identity() -> None:
    detection = detect_intent_override(
        "Actually I want to schedule a new appointment instead.",
        {
            "appointment_management_mode": APPOINTMENT_MANAGEMENT_MODE_RESCHEDULE,
            "appointment_management_awaiting": APPOINTMENT_MANAGEMENT_AWAITING_PATIENT_IDENTITY,
        },
        parse_patient_fields=_parse_patient_fields,
        booking_identity_active=False,
    )

    assert detection.kind == "clear"
    assert detection.target is IntentSwitchTarget.BOOKING


def test_identity_message_does_not_trigger_switch() -> None:
    detection = detect_intent_override(
        "John Smith, 19/09/1996",
        {
            "appointment_management_mode": APPOINTMENT_MANAGEMENT_MODE_RESCHEDULE,
            "appointment_management_awaiting": APPOINTMENT_MANAGEMENT_AWAITING_PATIENT_IDENTITY,
        },
        parse_patient_fields=_parse_patient_fields,
        booking_identity_active=False,
    )

    assert detection.kind == "none"


def test_slot_time_input_does_not_trigger_switch() -> None:
    assert is_expected_in_flow_input(
        "Tuesday at 14",
        {
            "appointment_management_mode": APPOINTMENT_MANAGEMENT_MODE_RESCHEDULE,
            "appointment_management_awaiting": APPOINTMENT_MANAGEMENT_AWAITING_NEW_SLOT_SELECTION,
            "offered_slots": [{"reference": "1"}],
        },
        parse_patient_fields=_parse_patient_fields,
    )


def test_option_input_does_not_trigger_switch() -> None:
    awaiting_selection = APPOINTMENT_MANAGEMENT_AWAITING_APPOINTMENT_SELECTION
    assert is_expected_in_flow_input(
        "option 2",
        {
            "appointment_management_mode": APPOINTMENT_MANAGEMENT_MODE_RESCHEDULE,
            "appointment_management_awaiting": awaiting_selection,
            "offered_appointments": [{"id": "1"}, {"id": "2"}],
        },
        parse_patient_fields=_parse_patient_fields,
    )


def test_build_confirmation_message_for_uncertain_reschedule_to_booking() -> None:
    message = build_intent_switch_confirmation_message(
        from_flow=APPOINTMENT_MANAGEMENT_MODE_RESCHEDULE,
        target=IntentSwitchTarget.BOOKING,
    )

    assert "stop rescheduling" in message.lower()
    assert "booking a new appointment" in message.lower()


@pytest.fixture()
def scheduling_chat_service() -> ChatReceptionistService:
    repository = FakeConversationRepository()
    conversations = ConversationService(repository=repository)
    scheduling = create_demo_scheduling_service()
    return create_chat_receptionist_service(
        conversations=conversations,
        scheduling=scheduling,
    )


def test_clear_switch_from_reschedule_identity_to_booking(
    scheduling_chat_service: ChatReceptionistService,
) -> None:
    service = scheduling_chat_service

    started = service.handle_message(
        ChatMessageInput(message="I need to reschedule my appointment"),
    )
    result = service.handle_message(
        ChatMessageInput(
            message="Actually I want to schedule a new appointment instead.",
            conversation_id=started.conversation.id,
        ),
    )

    assert result.intent == ChatReceptionistIntent.APPOINTMENT_REQUEST
    assert "full name and date of birth" not in result.reply.lower()
    context = result.conversation.conversation_metadata["chat_context"]
    assert context.get("appointment_management_mode") is None
    assert context.get("appointment_management_awaiting") is None
    assert context.get(PENDING_INTENT_SWITCH_KEY) is None


def test_identity_during_reschedule_identity_proceeds_without_switch() -> None:
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

    started = service.handle_message(
        ChatMessageInput(message="I need to reschedule my appointment"),
    )
    result = service.handle_message(
        ChatMessageInput(
            message="Felipe Godoy, 19/09/1996",
            conversation_id=started.conversation.id,
        ),
    )

    assert result.intent == ChatReceptionistIntent.RESCHEDULE_REQUEST
    context = result.conversation.conversation_metadata["chat_context"]
    assert context["appointment_management_mode"] == APPOINTMENT_MANAGEMENT_MODE_RESCHEDULE
    assert (
        context["appointment_management_awaiting"]
        == APPOINTMENT_MANAGEMENT_AWAITING_APPOINTMENT_SELECTION
    )


def test_clear_switch_from_cancellation_to_reschedule() -> None:
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

    started = service.handle_message(
        ChatMessageInput(message="I need to cancel my appointment"),
    )
    result = service.handle_message(
        ChatMessageInput(
            message="Actually I want to reschedule instead.",
            conversation_id=started.conversation.id,
        ),
    )

    assert result.intent == ChatReceptionistIntent.RESCHEDULE_REQUEST
    context = result.conversation.conversation_metadata["chat_context"]
    assert context["appointment_management_mode"] == APPOINTMENT_MANAGEMENT_MODE_RESCHEDULE
    assert context.get("appointment_management_mode") != APPOINTMENT_MANAGEMENT_MODE_CANCEL
    assert context.get(PENDING_INTENT_SWITCH_KEY) is None


def test_clear_switch_from_lookup_identity_to_booking() -> None:
    service, _repository, _patient, _emily, _reed = _create_chat_service_with_patient()

    started = service.handle_message(
        ChatMessageInput(message="show my upcoming appointments"),
    )
    result = service.handle_message(
        ChatMessageInput(
            message="I want to book another appointment.",
            conversation_id=started.conversation.id,
        ),
    )

    assert result.intent == ChatReceptionistIntent.APPOINTMENT_REQUEST
    context = result.conversation.conversation_metadata["chat_context"]
    assert context.get("appointment_management_mode") is None
    assert context.get(PENDING_INTENT_SWITCH_KEY) is None


def test_uncertain_switch_stores_pending_confirmation() -> None:
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

    started = service.handle_message(
        ChatMessageInput(message="I need to reschedule my appointment"),
    )
    result = service.handle_message(
        ChatMessageInput(
            message="Maybe I should just book another one.",
            conversation_id=started.conversation.id,
        ),
    )

    context = result.conversation.conversation_metadata["chat_context"]
    pending = context.get(PENDING_INTENT_SWITCH_KEY)
    assert isinstance(pending, dict)
    assert pending.get("to_intent") == IntentSwitchTarget.BOOKING.value
    assert pending.get("from_flow") == APPOINTMENT_MANAGEMENT_MODE_RESCHEDULE
    assert "stop rescheduling" in result.reply.lower()
    assert context["appointment_management_mode"] == APPOINTMENT_MANAGEMENT_MODE_RESCHEDULE


def test_pending_switch_yes_performs_switch() -> None:
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

    started = service.handle_message(
        ChatMessageInput(message="I need to reschedule my appointment"),
    )
    service.handle_message(
        ChatMessageInput(
            message="Maybe I should just book another one.",
            conversation_id=started.conversation.id,
        ),
    )
    result = service.handle_message(
        ChatMessageInput(message="yes", conversation_id=started.conversation.id),
    )

    assert result.intent == ChatReceptionistIntent.APPOINTMENT_REQUEST
    context = result.conversation.conversation_metadata["chat_context"]
    assert context.get("appointment_management_mode") is None
    assert context.get(PENDING_INTENT_SWITCH_KEY) is None


def test_pending_switch_no_resumes_previous_flow() -> None:
    service = create_chat_receptionist_service(
        conversations=ConversationService(repository=FakeConversationRepository()),
        scheduling=create_demo_scheduling_service(),
    )

    started = service.handle_message(
        ChatMessageInput(message="I need to reschedule my appointment"),
    )
    service.handle_message(
        ChatMessageInput(
            message="Maybe I should just book another one.",
            conversation_id=started.conversation.id,
        ),
    )
    result = service.handle_message(
        ChatMessageInput(message="no", conversation_id=started.conversation.id),
    )

    assert result.intent == ChatReceptionistIntent.RESCHEDULE_REQUEST
    assert "full name" in result.reply.lower()
    context = result.conversation.conversation_metadata["chat_context"]
    assert context.get(PENDING_INTENT_SWITCH_KEY) is None
    assert context["appointment_management_mode"] == APPOINTMENT_MANAGEMENT_MODE_RESCHEDULE
    assert (
        context["appointment_management_awaiting"]
        == APPOINTMENT_MANAGEMENT_AWAITING_PATIENT_IDENTITY
    )


def test_reschedule_slot_selection_time_input_stays_in_flow() -> None:
    service, _repository, emily, patient = _reschedule_wednesday_pm_service()
    appointments = [
        _wednesday_appointment(
            patient_id=patient.id,
            doctor_id=emily.id,
            specialty_id=emily.specialty_id,
        ),
    ]
    _slot_state, conversation_id = _reach_reschedule_new_slot_selection(
        service,
        appointments=appointments,
    )

    result = service.handle_message(
        ChatMessageInput(message="Tuesday at 14", conversation_id=conversation_id),
    )

    context = result.conversation.conversation_metadata["chat_context"]
    assert context["appointment_management_mode"] == APPOINTMENT_MANAGEMENT_MODE_RESCHEDULE
    # The time input is consumed by existing reschedule slot-selection behavior
    # (no intent switch): it either stays awaiting selection or advances to the
    # reschedule confirmation when it resolves to an offered slot.
    assert context["appointment_management_awaiting"] in {
        APPOINTMENT_MANAGEMENT_AWAITING_NEW_SLOT_SELECTION,
        APPOINTMENT_MANAGEMENT_AWAITING_RESCHEDULE_CONFIRMATION,
    }
    assert context.get(PENDING_INTENT_SWITCH_KEY) is None


def test_appointment_selection_option_stays_in_flow() -> None:
    service, _repository, patient, emily, reed = _create_chat_service_with_patient()
    appointments = [
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
    ]
    _selection_state, conversation_id = _reach_reschedule_appointment_selection(
        service,
        appointments=appointments,
    )

    result = service.handle_message(
        ChatMessageInput(message="option 2", conversation_id=conversation_id),
    )

    context = result.conversation.conversation_metadata["chat_context"]
    assert context["appointment_management_mode"] == APPOINTMENT_MANAGEMENT_MODE_RESCHEDULE
    # The option input is handled by existing reschedule selection behavior (no
    # intent switch): it stays in appointment selection or advances to the new
    # time preference once a listed appointment is chosen.
    assert context["appointment_management_awaiting"] in {
        APPOINTMENT_MANAGEMENT_AWAITING_APPOINTMENT_SELECTION,
        APPOINTMENT_MANAGEMENT_AWAITING_NEW_TIME_PREFERENCE,
    }
    assert context.get(PENDING_INTENT_SWITCH_KEY) is None


def test_human_escalation_wins_over_pending_intent_switch() -> None:
    repository = FakeConversationRepository()
    conversations = ConversationService(repository=repository)
    scheduling = create_demo_scheduling_service()
    human_escalations = HumanEscalationService(
        repository=FakeHumanEscalationRepository(),
    )
    service = create_chat_receptionist_service(
        conversations=conversations,
        scheduling=scheduling,
        conversation_health=ConversationHealthService(),
        human_escalations=human_escalations,
    )

    started = service.handle_message(
        ChatMessageInput(message="I need to reschedule my appointment"),
    )
    service.handle_message(
        ChatMessageInput(
            message="Maybe I should just book another one.",
            conversation_id=started.conversation.id,
        ),
    )
    result = service.handle_message(
        ChatMessageInput(message="Human please", conversation_id=started.conversation.id),
    )

    assert result.intent == ChatReceptionistIntent.HUMAN_ESCALATION_REQUESTED
    context = result.conversation.conversation_metadata["chat_context"]
    assert context.get("appointment_management_mode") is None
    assert context.get(PENDING_INTENT_SWITCH_KEY) is None


def test_post_lookup_booking_request_still_routes_to_scheduling() -> None:
    service, _repository, patient, emily, _reed = _create_chat_service_with_patient()
    appointment = Appointment(
        patient_id=patient.id,
        doctor_id=emily.id,
        specialty_id=emily.specialty_id,
        start_time=datetime(2026, 7, 8, 10, 0, tzinfo=UTC),
        end_time=datetime(2026, 7, 8, 10, 30, tzinfo=UTC),
        status=AppointmentStatus.SCHEDULED,
        reason="Follow-up",
    )
    _add_appointments(service, [appointment])

    started = service.handle_message(
        ChatMessageInput(message="show my upcoming appointments"),
    )
    service.handle_message(
        ChatMessageInput(
            message="Felipe Godoy, 1996-09-19",
            conversation_id=started.conversation.id,
        ),
    )
    result = service.handle_message(
        ChatMessageInput(
            message="I want to book another appointment.",
            conversation_id=started.conversation.id,
        ),
    )

    assert result.intent == ChatReceptionistIntent.APPOINTMENT_REQUEST
    context = result.conversation.conversation_metadata["chat_context"]
    assert context.get("appointment_management_mode") is None
    assert context.get("lookup_status") is None
