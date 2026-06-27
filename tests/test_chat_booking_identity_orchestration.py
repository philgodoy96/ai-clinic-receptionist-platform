from __future__ import annotations

from typing import cast

import pytest

from app.models.conversations import Conversation
from app.models.scheduling import Patient
from app.services.appointment_booking import AppointmentBookingService
from app.services.chat_appointment_cancellation import (
    APPOINTMENT_MANAGEMENT_AWAITING_PATIENT_IDENTITY,
    APPOINTMENT_MANAGEMENT_MODE_CANCEL,
)
from app.services.chat_appointment_rescheduling import APPOINTMENT_MANAGEMENT_MODE_RESCHEDULE
from app.services.chat_booking_identity import ChatBookingIdentityStep
from app.services.chat_receptionist import (
    ChatMessageInput,
    ChatReceptionistIntent,
    ChatReceptionistService,
)
from app.services.conversations import ConversationService
from app.services.scheduling import SchedulingService
from tests.chat_booking_flow_support import (
    EXISTING_PATIENT_IDENTITY_STEPS,
    advance_new_patient_to_booking_summary,
    complete_existing_patient_booking,
    complete_new_patient_booking,
    conversation_with_active_hold,
    send_chat_messages,
)
from tests.test_chat_receptionist_service import (
    TrackingAppointmentBookingService,
    _create_hold_service,
    create_appointment_booking_service_for_scheduling,
    create_chat_receptionist_service,
)
from tests.test_conversations import FakeConversationRepository
from tests.test_scheduling_services import (
    EMILY_JULY_SLOT_3_ID,
    FakePatientRepository,
    create_demo_scheduling_service_with_emily_july_availability,
    create_demo_scheduling_service_with_emily_mixed_july_availability,
    create_patient,
)


def _create_service(
    *,
    patients: list[Patient] | None = None,
    mixed_slots: bool = False,
) -> tuple[ChatReceptionistService, TrackingAppointmentBookingService, SchedulingService]:
    repository = FakeConversationRepository()
    conversations = ConversationService(repository=repository)
    hold_service = _create_hold_service()
    if mixed_slots:
        scheduling = create_demo_scheduling_service_with_emily_mixed_july_availability(
            patients=patients or [],
        )
    else:
        scheduling = create_demo_scheduling_service_with_emily_july_availability(
            patients=patients or [],
        )
    inner_booking = create_appointment_booking_service_for_scheduling(
        scheduling,
        hold_service,
    )
    tracking_booking = TrackingAppointmentBookingService(inner_booking)
    service = create_chat_receptionist_service(
        conversations=conversations,
        scheduling=scheduling,
        hold_service=hold_service,
        appointment_booking=cast(AppointmentBookingService, tracking_booking),
    )
    return service, tracking_booking, scheduling


def test_after_hold_assistant_asks_if_patient_has_been_seen_before() -> None:
    service, _tracking, _scheduling = _create_service()

    conversation = conversation_with_active_hold(service)
    result = service.handle_message(
        ChatMessageInput(message="hello", conversation_id=conversation.id),
    )

    assert result.intent == ChatReceptionistIntent.PATIENT_IDENTITY_PARTIAL
    reply = result.reply.lower()
    assert "seen" in reply or "before" in reply
    assert "on file" not in reply


def test_new_patient_happy_path_books_and_queues_identity() -> None:
    service, tracking_booking, scheduling = _create_service()
    conversation = conversation_with_active_hold(service)

    summary = advance_new_patient_to_booking_summary(service, conversation)
    assert summary.intent == ChatReceptionistIntent.BOOKING_CONFIRMATION_REQUIRED
    assert "before i book" in summary.reply.lower()
    assert "jane doe" in summary.reply.lower()
    assert "on file" not in summary.reply.lower()
    assert tracking_booking.book_calls == []

    booked = service.handle_message(
        ChatMessageInput(message="Yes", conversation_id=conversation.id),
    )
    assert booked.intent == ChatReceptionistIntent.BOOKING_CONFIRMED
    assert booked.booking_confirmed is True
    assert len(tracking_booking.book_calls) == 1
    context = booked.conversation.conversation_metadata["chat_context"]
    assert context.get("patient_resolution_id")
    assert context.get("confirmed_booking_email") == "jane.doe@example.com"
    booked_patient_id = booked.booked_patient_id
    assert booked_patient_id is not None
    patient = scheduling.patients.get_by_id(booked_patient_id)
    assert patient is not None
    assert patient.email == "jane.doe@example.com"


def test_existing_patient_happy_path_books_with_seed_identity() -> None:
    john = create_patient()
    service, tracking_booking, _scheduling = _create_service(patients=[john])
    conversation = conversation_with_active_hold(service)

    result = complete_existing_patient_booking(service, conversation)
    assert result.intent == ChatReceptionistIntent.BOOKING_CONFIRMED
    assert result.booking_confirmed is True
    assert len(tracking_booking.book_calls) == 1
    assert "on file" not in result.reply.lower()


def test_on_file_copy_not_used_before_identity_resolution() -> None:
    service, _tracking, _scheduling = _create_service()
    conversation = conversation_with_active_hold(service)

    for message in ("No.", "Jane Doe", "1990-05-15"):
        result = service.handle_message(
            ChatMessageInput(message=message, conversation_id=conversation.id),
        )
        assert "on file" not in result.reply.lower()


def test_unknown_jane_doe_succeeds_as_new_patient_with_demo_creation() -> None:
    service, tracking_booking, _scheduling = _create_service(patients=[])
    conversation = conversation_with_active_hold(service)

    result = complete_new_patient_booking(service, conversation)
    assert result.intent == ChatReceptionistIntent.BOOKING_CONFIRMED
    assert len(tracking_booking.book_calls) == 1


def test_booking_does_not_happen_before_email_confirmation() -> None:
    service, tracking_booking, _scheduling = _create_service()
    conversation = conversation_with_active_hold(service)

    send_chat_messages(
        service,
        conversation.id,
        ("No.", "Jane Doe", "1990-05-15", "jane.doe@example.com"),
    )
    result = service.handle_message(
        ChatMessageInput(message="Yes, please book it.", conversation_id=conversation.id),
    )

    assert result.intent != ChatReceptionistIntent.BOOKING_CONFIRMED
    assert tracking_booking.book_calls == []


def test_booking_does_not_happen_before_final_confirmation() -> None:
    service, tracking_booking, _scheduling = _create_service()
    conversation = conversation_with_active_hold(service)

    summary = advance_new_patient_to_booking_summary(service, conversation)
    assert summary.intent == ChatReceptionistIntent.BOOKING_CONFIRMATION_REQUIRED
    assert tracking_booking.book_calls == []


def test_user_facing_reply_does_not_expose_internal_details() -> None:
    service, _tracking, _scheduling = _create_service()
    conversation = conversation_with_active_hold(service)

    summary = advance_new_patient_to_booking_summary(service, conversation)
    reply = summary.reply.lower()
    for forbidden in (
        "hold_id",
        "slot_id",
        "patient_resolution_id",
        "redis",
        "uuid",
        "resolve_patient",
    ):
        assert forbidden not in reply


def test_hold_without_identity_step_fails_closed_on_booking_attempt() -> None:
    service, tracking_booking, _scheduling = _create_service()
    conversation = conversation_with_active_hold(service)
    chat_context = conversation.conversation_metadata["chat_context"]
    chat_context.pop("booking_identity_step", None)
    conversation.conversation_metadata["chat_context"] = chat_context

    result = service.handle_message(
        ChatMessageInput(
            message="Jane Doe, 1990-05-15, jane.doe@example.com. Please confirm.",
            conversation_id=conversation.id,
        ),
    )

    assert result.intent == ChatReceptionistIntent.BOOKING_IDENTITY_MISSING
    assert tracking_booking.book_calls == []
    assert "secure booking flow" in result.reply.lower()


def test_chat_receptionist_service_requires_patient_identity_resolution() -> None:
    repository = FakeConversationRepository()
    conversations = ConversationService(repository=repository)
    scheduling = create_demo_scheduling_service_with_emily_july_availability()
    holds = _create_hold_service()

    with pytest.raises(TypeError):
        ChatReceptionistService(  # type: ignore[call-arg]
            conversations=conversations,
            scheduling=scheduling,
            appointment_holds=holds,
            appointment_booking=create_appointment_booking_service_for_scheduling(
                scheduling,
                holds,
            ),
        )


def test_14_00_slot_new_patient_postman_style_flow() -> None:
    service, tracking_booking, _scheduling = _create_service(mixed_slots=True)
    availability = service.handle_message(
        ChatMessageInput(
            message="I want to book dermatology with Dr. Emily Carter on 2026-07-02",
        ),
    )
    hold = service.handle_message(
        ChatMessageInput(message="14:00", conversation_id=availability.conversation.id),
    )
    conversation = hold.conversation

    result = complete_new_patient_booking(service, conversation)
    assert result.intent == ChatReceptionistIntent.BOOKING_CONFIRMED
    assert len(tracking_booking.book_calls) == 1
    assert tracking_booking.book_calls[0].availability_slot_id == EMILY_JULY_SLOT_3_ID


def test_final_booking_summary_contains_real_facts_not_placeholders() -> None:
    service, _tracking, _scheduling = _create_service(mixed_slots=True)
    availability = service.handle_message(
        ChatMessageInput(
            message="I want to book dermatology with Dr. Emily Carter on 2026-07-02",
        ),
    )
    hold = service.handle_message(
        ChatMessageInput(message="14:00", conversation_id=availability.conversation.id),
    )
    conversation = hold.conversation

    summary = advance_new_patient_to_booking_summary(service, conversation)
    reply = summary.reply

    assert summary.intent == ChatReceptionistIntent.BOOKING_CONFIRMATION_REQUIRED
    assert "selected doctor" not in reply.lower()
    assert "selected date" not in reply.lower()
    assert "selected time" not in reply.lower()
    assert "Dr. Emily Carter" in reply
    assert "2026-07-02" in reply
    assert "14:00" in reply
    assert "Jane Doe" in reply
    assert "Jane Doe." not in reply
    assert "jane.doe@example.com" in reply


def test_bare_yes_confirms_final_booking() -> None:
    service, tracking_booking, _scheduling = _create_service(mixed_slots=True)
    availability = service.handle_message(
        ChatMessageInput(
            message="I want to book dermatology with Dr. Emily Carter on 2026-07-02",
        ),
    )
    hold = service.handle_message(
        ChatMessageInput(message="14:00", conversation_id=availability.conversation.id),
    )
    conversation = hold.conversation

    advance_new_patient_to_booking_summary(service, conversation)
    booked = service.handle_message(
        ChatMessageInput(message="Yes", conversation_id=conversation.id),
    )

    assert booked.intent == ChatReceptionistIntent.BOOKING_CONFIRMED
    assert booked.booking_confirmed is True
    assert booked.appointment_id is not None
    assert len(tracking_booking.book_calls) == 1
    assert "booked" in booked.reply.lower() or "confirmed" in booked.reply.lower()


def test_repeated_yes_after_booking_does_not_create_duplicate() -> None:
    service, tracking_booking, _scheduling = _create_service(mixed_slots=True)
    availability = service.handle_message(
        ChatMessageInput(
            message="I want to book dermatology with Dr. Emily Carter on 2026-07-02",
        ),
    )
    hold = service.handle_message(
        ChatMessageInput(message="14:00", conversation_id=availability.conversation.id),
    )
    conversation = hold.conversation

    booked = complete_new_patient_booking(service, conversation)
    assert booked.booking_confirmed is True
    assert len(tracking_booking.book_calls) == 1

    repeated = service.handle_message(
        ChatMessageInput(message="Yes", conversation_id=conversation.id),
    )
    assert repeated.intent == ChatReceptionistIntent.BOOKING_CONFIRMED
    assert len(tracking_booking.book_calls) == 1
    reply = repeated.reply.lower()
    assert "schedule, cancel, or reschedule" in reply
    assert "already confirmed" not in reply


def test_existing_patient_bare_yes_confirms_final_booking() -> None:
    john = create_patient()
    service, tracking_booking, _scheduling = _create_service(patients=[john])
    conversation = conversation_with_active_hold(service)

    send_chat_messages(service, conversation.id, EXISTING_PATIENT_IDENTITY_STEPS)
    booked = service.handle_message(
        ChatMessageInput(message="Yes", conversation_id=conversation.id),
    )

    assert booked.intent == ChatReceptionistIntent.BOOKING_CONFIRMED
    assert booked.booking_confirmed is True
    assert len(tracking_booking.book_calls) == 1


def _book_appointment_and_get_conversation(
    service: ChatReceptionistService,
) -> Conversation:
    availability = service.handle_message(
        ChatMessageInput(
            message="I want to book dermatology with Dr. Emily Carter on 2026-07-02",
        ),
    )
    hold = service.handle_message(
        ChatMessageInput(message="14:00", conversation_id=availability.conversation.id),
    )
    conversation = hold.conversation
    booked = complete_new_patient_booking(service, conversation)
    assert booked.booking_confirmed is True
    assert booked.appointment_id is not None
    return conversation


@pytest.mark.parametrize(
    "closing_message",
    ["no", "nah", "that's all", "I'm good"],
)
def test_post_booking_closing_response_ends_conversation(closing_message: str) -> None:
    service, tracking_booking, _scheduling = _create_service(mixed_slots=True)
    conversation = _book_appointment_and_get_conversation(service)

    result = service.handle_message(
        ChatMessageInput(message=closing_message, conversation_id=conversation.id),
    )

    reply = result.reply.lower()
    assert result.intent == ChatReceptionistIntent.BOOKING_CONFIRMED
    assert "you're all set" in reply
    assert "is there anything else" not in reply
    assert "already confirmed" not in reply
    assert len(tracking_booking.book_calls) == 1


def test_post_booking_unclear_message_keeps_help_prompt() -> None:
    service, tracking_booking, _scheduling = _create_service(mixed_slots=True)
    conversation = _book_appointment_and_get_conversation(service)

    result = service.handle_message(
        ChatMessageInput(
            message="Can you tell me a joke?",
            conversation_id=conversation.id,
        ),
    )

    reply = result.reply.lower()
    assert result.intent == ChatReceptionistIntent.BOOKING_CONFIRMED
    assert "your appointment is confirmed" in reply
    assert "schedule, cancel, or reschedule" in reply
    assert "is there anything else" not in reply
    assert len(tracking_booking.book_calls) == 1


def test_post_booking_yes_asks_what_kind_of_help_is_needed() -> None:
    service, tracking_booking, _scheduling = _create_service(mixed_slots=True)
    conversation = _book_appointment_and_get_conversation(service)

    result = service.handle_message(
        ChatMessageInput(message="yes", conversation_id=conversation.id),
    )

    reply = result.reply.lower()
    assert result.intent == ChatReceptionistIntent.BOOKING_CONFIRMED
    assert "schedule, cancel, or reschedule" in reply
    assert "you're all set" not in reply
    assert len(tracking_booking.book_calls) == 1


def test_post_booking_new_scheduling_request_is_not_blocked() -> None:
    service, tracking_booking, _scheduling = _create_service(mixed_slots=True)
    conversation = _book_appointment_and_get_conversation(service)

    result = service.handle_message(
        ChatMessageInput(
            message="I want to schedule another appointment",
            conversation_id=conversation.id,
        ),
    )

    reply = result.reply.lower()
    assert "already confirmed" not in reply
    assert result.intent != ChatReceptionistIntent.BOOKING_CONFIRMED
    # The confirmed appointment record is preserved and nothing new is booked.
    assert len(tracking_booking.book_calls) == 1
    context = result.conversation.conversation_metadata["chat_context"]
    assert context.get("appointment_id")


def test_post_booking_cancel_request_routes_to_cancellation() -> None:
    service, tracking_booking, _scheduling = _create_service(mixed_slots=True)
    conversation = _book_appointment_and_get_conversation(service)

    result = service.handle_message(
        ChatMessageInput(
            message="I want to cancel",
            conversation_id=conversation.id,
        ),
    )

    reply = result.reply.lower()
    assert result.intent == ChatReceptionistIntent.CANCEL_REQUEST
    assert "full name" in reply
    assert "date of birth" in reply
    assert "already confirmed" not in reply
    assert len(tracking_booking.book_calls) == 1
    context = result.conversation.conversation_metadata["chat_context"]
    assert context["appointment_management_mode"] == APPOINTMENT_MANAGEMENT_MODE_CANCEL
    assert (
        context["appointment_management_awaiting"]
        == APPOINTMENT_MANAGEMENT_AWAITING_PATIENT_IDENTITY
    )


def test_new_patient_ambiguous_numeric_dob_asks_for_clarification() -> None:
    service, tracking_booking, scheduling = _create_service(patients=[])
    conversation = conversation_with_active_hold(service)

    send_chat_messages(service, conversation.id, ("No.", "Jane Doe."))
    result = service.handle_message(
        ChatMessageInput(message="09/08/1980", conversation_id=conversation.id),
    )

    reply = result.reply.lower()
    assert result.intent == ChatReceptionistIntent.BOOKING_IDENTITY_MISSING
    assert "september 8, 1980" in reply
    assert "august 9, 1980" in reply
    # The flow stays on the DOB step; no patient is created or looked up and no
    # booking is attempted while the ambiguity is unresolved.
    assert tracking_booking.book_calls == []
    chat_context = result.conversation.conversation_metadata["chat_context"]
    assert (
        chat_context["booking_identity_step"]
        == ChatBookingIdentityStep.COLLECT_NEW_DOB.value
    )
    patients_repo = cast(FakePatientRepository, scheduling.patients)
    assert patients_repo.patients == []


def test_existing_patient_ambiguous_numeric_dob_does_not_look_up_patient() -> None:
    service, tracking_booking, _scheduling = _create_service(patients=[])
    conversation = conversation_with_active_hold(service)

    send_chat_messages(service, conversation.id, ("Yes.",))
    result = service.handle_message(
        ChatMessageInput(
            message="Jane Doe, 09/08/1980",
            conversation_id=conversation.id,
        ),
    )

    reply = result.reply.lower()
    assert result.intent == ChatReceptionistIntent.BOOKING_IDENTITY_MISSING
    assert "september 8, 1980" in reply
    assert "august 9, 1980" in reply
    assert tracking_booking.book_calls == []
    chat_context = result.conversation.conversation_metadata["chat_context"]
    # The already-collected name is preserved, but no resolution happened.
    assert chat_context["patient_identity"]["full_name"] == "Jane Doe"
    assert "date_of_birth" not in chat_context["patient_identity"]
    assert "patient_resolution_id" not in chat_context


def test_new_patient_iso_dob_is_accepted_without_clarification() -> None:
    service, _tracking, _scheduling = _create_service(patients=[])
    conversation = conversation_with_active_hold(service)

    send_chat_messages(service, conversation.id, ("No.", "Jane Doe."))
    result = service.handle_message(
        ChatMessageInput(message="1980-09-08", conversation_id=conversation.id),
    )

    reply = result.reply.lower()
    assert result.intent == ChatReceptionistIntent.PATIENT_IDENTITY_PARTIAL
    assert "could mean" not in reply
    assert "email" in reply
    chat_context = result.conversation.conversation_metadata["chat_context"]
    assert chat_context["patient_identity"]["date_of_birth"] == "1980-09-08"


def test_new_patient_month_name_dob_is_accepted_without_clarification() -> None:
    service, _tracking, _scheduling = _create_service(patients=[])
    conversation = conversation_with_active_hold(service)

    send_chat_messages(service, conversation.id, ("No.", "Jane Doe."))
    result = service.handle_message(
        ChatMessageInput(message="September 8, 1980", conversation_id=conversation.id),
    )

    reply = result.reply.lower()
    assert result.intent == ChatReceptionistIntent.PATIENT_IDENTITY_PARTIAL
    assert "could mean" not in reply
    assert "email" in reply
    chat_context = result.conversation.conversation_metadata["chat_context"]
    assert chat_context["patient_identity"]["date_of_birth"] == "1980-09-08"


def test_post_booking_reschedule_request_routes_to_reschedule() -> None:
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
