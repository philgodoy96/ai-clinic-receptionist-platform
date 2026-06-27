from __future__ import annotations

import re
from datetime import date
from typing import cast
from unittest.mock import patch
from uuid import uuid4

import pytest
from httpx import Response
from starlette.testclient import TestClient

from app.models.conversations import Conversation
from app.models.scheduling import Patient
from app.services.appointment_booking import (
    AppointmentBookingService,
    AppointmentSlotAlreadyBookedError,
)
from app.services.appointment_holds import (
    AppointmentHoldNotFoundError,
    AppointmentSlotAlreadyHeldError,
)
from app.services.chat_receptionist import (
    ChatMessageInput,
    ChatReceptionistIntent,
    ChatReceptionistService,
)
from app.services.conversations import ConversationService
from app.services.email_jobs import (
    AppointmentConfirmationEmailJobCreate,
    EmailJobService,
)
from app.services.scheduling import SchedulingService
from tests.chat_booking_flow_support import (
    FINAL_BOOKING_CONFIRM,
    NEW_PATIENT_IDENTITY_STEPS,
    advance_new_patient_to_booking_summary,
    complete_new_patient_booking,
    conversation_with_active_hold,
)
from tests.test_appointment_holds import FakeAppointmentHoldRepository
from tests.test_chat_receptionist_service import (
    FakeAppointmentHoldService,
    TrackingAppointmentBookingService,
    _create_hold_service,
    create_appointment_booking_service_for_scheduling,
    create_chat_receptionist_service,
)
from tests.test_conversations import FakeConversationRepository
from tests.test_email_jobs import FakeEmailJobRepository
from tests.test_scheduling_services import (
    EMILY_JULY_SLOT_1_ID,
    FakeAppointmentRepository,
    create_demo_scheduling_service_with_emily_july_availability,
)

FULL_IDENTITY_MESSAGE = "Jane Doe, 1990-05-15, +1 555-123-4567, jane.doe@example.com"
FULL_IDENTITY_WITH_CONFIRM = f"{FULL_IDENTITY_MESSAGE}. Please confirm."

_UUID_PATTERN = re.compile(
    r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}",
)


def _expire_active_hold(hold_service: FakeAppointmentHoldService) -> None:
    """Simulate the temporary hold expiring out of the store (TTL elapsed)."""
    repository = cast(FakeAppointmentHoldRepository, hold_service.repository)
    repository.holds.clear()


def create_jane_doe_patient() -> Patient:
    return Patient(
        id=uuid4(),
        full_name="Jane Doe",
        date_of_birth=date(1990, 5, 15),
        phone_number="+1 555-123-4567",
        email="jane.doe@example.com",
    )


@pytest.fixture()
def booking_flow_context() -> tuple[
    ChatReceptionistService,
    TrackingAppointmentBookingService,
    FakeAppointmentHoldService,
    SchedulingService,
]:
    repository = FakeConversationRepository()
    conversations = ConversationService(repository=repository)
    hold_service = _create_hold_service()
    scheduling = create_demo_scheduling_service_with_emily_july_availability(
        patients=[],
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
    return service, tracking_booking, hold_service, scheduling


def _conversation_with_active_hold(service: ChatReceptionistService) -> Conversation:
    return conversation_with_active_hold(service)


def test_confirm_without_hold_returns_booking_hold_missing_and_does_not_book(
    booking_flow_context: tuple[
        ChatReceptionistService,
        TrackingAppointmentBookingService,
        FakeAppointmentHoldService,
        SchedulingService,
    ],
) -> None:
    service, tracking_booking, _hold_service, _scheduling = booking_flow_context

    result = service.handle_message(ChatMessageInput(message="confirm"))

    assert result.intent == ChatReceptionistIntent.BOOKING_HOLD_MISSING
    assert tracking_booking.book_calls == []


def test_hold_exists_but_identity_missing_on_confirm(
    booking_flow_context: tuple[
        ChatReceptionistService,
        TrackingAppointmentBookingService,
        FakeAppointmentHoldService,
        SchedulingService,
    ],
) -> None:
    service, tracking_booking, _hold_service, _scheduling = booking_flow_context
    conversation = _conversation_with_active_hold(service)

    result = service.handle_message(
        ChatMessageInput(message="confirm", conversation_id=conversation.id),
    )

    assert result.intent == ChatReceptionistIntent.PATIENT_IDENTITY_PARTIAL
    assert tracking_booking.book_calls == []
    reply = result.reply.lower()
    assert "seen" in reply or "before" in reply


def test_partial_identity_is_stored_in_chat_context(
    booking_flow_context: tuple[
        ChatReceptionistService,
        TrackingAppointmentBookingService,
        FakeAppointmentHoldService,
        SchedulingService,
    ],
) -> None:
    service, tracking_booking, _hold_service, _scheduling = booking_flow_context

    result = service.handle_message(
        ChatMessageInput(message="jane.doe@example.com"),
    )

    patient_identity = result.conversation.conversation_metadata["chat_context"]["patient_identity"]

    assert patient_identity["email"] == "jane.doe@example.com"
    assert "full_name" not in patient_identity
    assert result.intent == ChatReceptionistIntent.PATIENT_IDENTITY_PARTIAL
    assert tracking_booking.book_calls == []


def test_complete_identity_without_confirmation_requests_confirmation_and_does_not_book(
    booking_flow_context: tuple[
        ChatReceptionistService,
        TrackingAppointmentBookingService,
        FakeAppointmentHoldService,
        SchedulingService,
    ],
) -> None:
    service, tracking_booking, _hold_service, _scheduling = booking_flow_context
    conversation = _conversation_with_active_hold(service)

    result = service.handle_message(
        ChatMessageInput(
            message=FULL_IDENTITY_MESSAGE,
            conversation_id=conversation.id,
        ),
    )

    assert result.intent == ChatReceptionistIntent.PATIENT_IDENTITY_PARTIAL
    assert "on file" not in result.reply.lower()
    assert tracking_booking.book_calls == []


def test_complete_identity_with_confirmation_books_appointment_without_llm(
    booking_flow_context: tuple[
        ChatReceptionistService,
        TrackingAppointmentBookingService,
        FakeAppointmentHoldService,
        SchedulingService,
    ],
) -> None:
    service, tracking_booking, _hold_service, scheduling = booking_flow_context
    appointments = scheduling.appointments
    assert isinstance(appointments, FakeAppointmentRepository)
    conversation = _conversation_with_active_hold(service)

    with patch("app.services.chat_receptionist.openai", create=True) as openai_mock:
        result = complete_new_patient_booking(service, conversation)

    assert openai_mock.call_count == 0
    assert result.intent == ChatReceptionistIntent.BOOKING_CONFIRMED
    assert result.booking_confirmed is True
    assert len(tracking_booking.book_calls) == 1
    assert result.conversation.conversation_metadata["chat_context"]["appointment_id"]
    assert appointments.appointments[0].availability_slot_id == EMILY_JULY_SLOT_1_ID


def test_booking_conflict_when_fake_booking_service_raises_conflict(
    booking_flow_context: tuple[
        ChatReceptionistService,
        TrackingAppointmentBookingService,
        FakeAppointmentHoldService,
        SchedulingService,
    ],
) -> None:
    service, tracking_booking, _hold_service, _scheduling = booking_flow_context
    tracking_booking.book_error = AppointmentSlotAlreadyBookedError(
        "doctor already has a scheduled appointment at this time",
    )
    conversation = _conversation_with_active_hold(service)

    result = complete_new_patient_booking(service, conversation)

    assert result.intent == ChatReceptionistIntent.BOOKING_CONFLICT
    assert len(tracking_booking.book_calls) == 1
    reply = result.reply.lower()
    assert "no longer available" in reply or "conflict" in reply


def test_hold_not_found_and_slot_cannot_be_rehold_clears_stale_state(
    booking_flow_context: tuple[
        ChatReceptionistService,
        TrackingAppointmentBookingService,
        FakeAppointmentHoldService,
        SchedulingService,
    ],
) -> None:
    service, tracking_booking, _hold_service, _scheduling = booking_flow_context
    # The booking service always reports the hold as gone, and the slot is still
    # occupied by the existing hold, so the slot cannot be re-held: unrecoverable.
    tracking_booking.book_error = AppointmentHoldNotFoundError(
        "appointment hold was not found or expired",
    )
    conversation = _conversation_with_active_hold(service)

    result = complete_new_patient_booking(service, conversation)

    assert result.intent == ChatReceptionistIntent.BOOKING_HOLD_EXPIRED
    assert result.booking_confirmed is False
    # No booking is ever completed without a valid hold.
    assert len(tracking_booking.book_calls) == 1
    reply = result.reply.lower()
    assert "no longer available" in reply
    assert "choose another" in reply
    assert "has been booked" not in reply
    chat_context = result.conversation.conversation_metadata["chat_context"]
    assert chat_context["hold_id"] is None
    assert chat_context["selected_availability_slot_id"] is None
    assert chat_context["selected_start_time"] is None
    assert chat_context.get("booking_identity_step") is None
    assert chat_context["appointment_intake_awaiting"] == "date_or_time_preference"


def test_complete_identity_with_confirmation_creates_one_idempotent_email_job(
    booking_flow_context: tuple[
        ChatReceptionistService,
        TrackingAppointmentBookingService,
        FakeAppointmentHoldService,
        SchedulingService,
    ],
) -> None:
    service, _tracking_booking, _hold_service, _scheduling = booking_flow_context
    email_job_repository = FakeEmailJobRepository()
    email_jobs = EmailJobService(repository=email_job_repository)
    conversation = _conversation_with_active_hold(service)

    result = complete_new_patient_booking(service, conversation)

    assert result.booking_confirmed is True
    assert result.appointment_id is not None
    assert result.booked_patient_id is not None
    assert result.booked_appointment_start_time is not None

    payload = AppointmentConfirmationEmailJobCreate(
        appointment_id=result.appointment_id,
        patient_id=result.booked_patient_id,
        appointment_start_time=result.booked_appointment_start_time.isoformat(),
        payload={"source": "chat_booking"},
    )
    first = email_jobs.get_or_create_appointment_confirmation_email_job(payload)
    second = email_jobs.get_or_create_appointment_confirmation_email_job(payload)

    assert first.created is True
    assert second.created is False
    assert second.email_job.id == first.email_job.id
    assert len(email_job_repository.email_jobs) == 1


def test_emergency_takes_priority_over_booking_confirmation(
    booking_flow_context: tuple[
        ChatReceptionistService,
        TrackingAppointmentBookingService,
        FakeAppointmentHoldService,
        SchedulingService,
    ],
) -> None:
    service, tracking_booking, _hold_service, _scheduling = booking_flow_context
    conversation = _conversation_with_active_hold(service)

    result = service.handle_message(
        ChatMessageInput(
            message=f"This is an emergency, {FULL_IDENTITY_WITH_CONFIRM}",
            conversation_id=conversation.id,
        ),
    )

    assert result.intent == ChatReceptionistIntent.EMERGENCY
    assert tracking_booking.book_calls == []


def _post_new_patient_booking_messages(
    client: TestClient,
    conversation_id: str,
) -> Response:
    for message in NEW_PATIENT_IDENTITY_STEPS:
        client.post(
            "/api/v1/chat/messages",
            json={"message": message, "conversation_id": conversation_id},
        )
    response: Response = client.post(
        "/api/v1/chat/messages",
        json={"message": FINAL_BOOKING_CONFIRM, "conversation_id": conversation_id},
    )
    return response


def test_successful_booking_api_creates_confirmation_email_job() -> None:
    from fastapi.testclient import TestClient

    from tests.demo_guardrail_support import (
        create_guarded_chat_app,
        make_chat_booking_guardrail_settings,
    )

    app, _, email_jobs = create_guarded_chat_app(
        make_chat_booking_guardrail_settings(),
        track_email_jobs=True,
    )

    with TestClient(app) as client:
        availability = client.post(
            "/api/v1/chat/messages",
            json={"message": "Dr. Emily Carter on 2026-07-02"},
        )
        conversation_id = availability.json()["conversation_id"]
        client.post(
            "/api/v1/chat/messages",
            json={
                "message": "I'll take 09:00",
                "conversation_id": conversation_id,
            },
        )
        booking = _post_new_patient_booking_messages(client, conversation_id)

    app.dependency_overrides.clear()

    assert booking.status_code == 200
    body = booking.json()
    assert body["booking_confirmed"] is True
    assert body["confirmation_email_queued"] is True
    assert email_jobs is not None
    assert len(email_jobs.jobs) == 1
    assert email_jobs.jobs[0].payload["source"] == "chat_booking"


def test_dispatch_publish_failure_does_not_rollback_booking() -> None:
    from collections.abc import Generator
    from typing import cast
    from uuid import UUID

    from fastapi.testclient import TestClient

    from app.api.dependencies import (
        get_appointment_hold_service,
        get_chat_receptionist_service,
        get_email_job_dispatch_publisher,
        get_email_job_service,
    )
    from app.db.session import get_db
    from app.main import create_app
    from app.messaging.email_job_dispatch import EmailJobDispatchPublisherError
    from app.services.conversations import ConversationService
    from app.services.email_jobs import EmailJobService
    from tests.test_chat_api import FakeDatabaseSession, FakeEmailJobService
    from tests.test_chat_receptionist_service import (
        FakeAppointmentHoldService,
        create_chat_receptionist_service,
    )
    from tests.test_conversations import FakeConversationRepository
    from tests.test_scheduling_services import (
        create_demo_scheduling_service_with_emily_july_availability,
    )

    class FailingDispatchPublisher:
        def publish_email_job_ready(self, *, email_job_id: UUID) -> None:
            raise EmailJobDispatchPublisherError("publish failed")

    app = create_app()
    repository = FakeConversationRepository()
    conversations = ConversationService(repository=repository)
    hold_service = FakeAppointmentHoldService()
    scheduling = create_demo_scheduling_service_with_emily_july_availability(
        patients=[],
    )
    chat_service = create_chat_receptionist_service(
        conversations=conversations,
        scheduling=scheduling,
        hold_service=hold_service,
    )
    email_jobs = FakeEmailJobService()
    db = FakeDatabaseSession()

    def override_db() -> Generator[FakeDatabaseSession, None, None]:
        yield db

    app.dependency_overrides[get_chat_receptionist_service] = lambda: chat_service
    app.dependency_overrides[get_appointment_hold_service] = lambda: hold_service
    app.dependency_overrides[get_db] = override_db
    app.dependency_overrides[get_email_job_service] = lambda: cast(
        EmailJobService,
        email_jobs,
    )
    app.dependency_overrides[get_email_job_dispatch_publisher] = lambda: FailingDispatchPublisher()

    with TestClient(app) as client:
        availability = client.post(
            "/api/v1/chat/messages",
            json={"message": "Dr. Emily Carter on 2026-07-02"},
        )
        conversation_id = availability.json()["conversation_id"]
        client.post(
            "/api/v1/chat/messages",
            json={
                "message": "I'll take 09:00",
                "conversation_id": conversation_id,
            },
        )
        booking = _post_new_patient_booking_messages(client, conversation_id)

    app.dependency_overrides.clear()

    assert booking.status_code == 200
    assert db.committed is True
    assert db.rolled_back is False
    body = booking.json()
    assert body["booking_confirmed"] is True
    assert body["appointment_id"]
    assert len(email_jobs.jobs) == 1


def test_demo_email_quota_exceeded_keeps_booking_without_confirmation_job() -> None:
    from fastapi.testclient import TestClient

    from tests.demo_guardrail_support import (
        FakeRedisClient,
        create_guarded_chat_app,
        make_chat_booking_guardrail_settings,
    )

    redis_client = FakeRedisClient()
    app, _, email_jobs = create_guarded_chat_app(
        make_chat_booking_guardrail_settings(
            DEMO_CONFIRMATION_EMAILS_PER_DAY_PER_IP=1,
            DEMO_GLOBAL_CONFIRMATION_EMAILS_PER_DAY=1,
        ),
        redis_client=redis_client,
        track_email_jobs=True,
    )
    email_ip_key = "demo_guardrail:email:ip:testclient:day:20260621"
    redis_client.values[email_ip_key] = 1

    with TestClient(app) as client:
        availability = client.post(
            "/api/v1/chat/messages",
            json={"message": "Dr. Emily Carter on 2026-07-02"},
        )
        conversation_id = availability.json()["conversation_id"]
        client.post(
            "/api/v1/chat/messages",
            json={
                "message": "I'll take 09:00",
                "conversation_id": conversation_id,
            },
        )
        booking = _post_new_patient_booking_messages(client, conversation_id)

    app.dependency_overrides.clear()

    assert booking.status_code == 200
    body = booking.json()
    assert body["booking_confirmed"] is True
    assert body["confirmation_email_queued"] is False
    assert email_jobs is not None
    assert email_jobs.jobs == []


def test_expired_hold_at_confirmation_refreshes_hold_and_books(
    booking_flow_context: tuple[
        ChatReceptionistService,
        TrackingAppointmentBookingService,
        FakeAppointmentHoldService,
        SchedulingService,
    ],
) -> None:
    service, tracking_booking, hold_service, scheduling = booking_flow_context
    appointments = scheduling.appointments
    assert isinstance(appointments, FakeAppointmentRepository)
    conversation = _conversation_with_active_hold(service)
    advance_new_patient_to_booking_summary(service, conversation)

    _expire_active_hold(hold_service)
    hold_service.create_hold_calls.clear()

    result = service.handle_message(
        ChatMessageInput(message=FINAL_BOOKING_CONFIRM, conversation_id=conversation.id),
    )

    assert result.intent == ChatReceptionistIntent.BOOKING_CONFIRMED
    assert result.booking_confirmed is True
    # Initial attempt fails (hold gone) and the refreshed hold is used to retry.
    assert len(tracking_booking.book_calls) == 2
    # A fresh hold is created during recovery, owned by the conversation.
    assert len(hold_service.create_hold_calls) == 1
    assert hold_service.create_hold_calls[0]["owner_id"] == str(conversation.id)
    assert hold_service.create_hold_calls[0]["availability_slot_id"] == EMILY_JULY_SLOT_1_ID
    chat_context = result.conversation.conversation_metadata["chat_context"]
    assert chat_context.get("appointment_id")
    assert appointments.appointments[0].availability_slot_id == EMILY_JULY_SLOT_1_ID
    assert not _UUID_PATTERN.search(result.reply)


def test_expired_hold_refresh_uses_conversation_owner_id(
    booking_flow_context: tuple[
        ChatReceptionistService,
        TrackingAppointmentBookingService,
        FakeAppointmentHoldService,
        SchedulingService,
    ],
) -> None:
    service, tracking_booking, hold_service, _scheduling = booking_flow_context
    conversation = _conversation_with_active_hold(service)
    advance_new_patient_to_booking_summary(service, conversation)

    _expire_active_hold(hold_service)
    hold_service.create_hold_calls.clear()

    result = service.handle_message(
        ChatMessageInput(message=FINAL_BOOKING_CONFIRM, conversation_id=conversation.id),
    )

    assert result.booking_confirmed is True
    retry_request = tracking_booking.book_calls[-1]
    assert retry_request.owner_id == str(conversation.id)
    assert hold_service.create_hold_calls[0]["owner_id"] == str(conversation.id)


def test_expired_hold_with_unavailable_slot_clears_state_and_does_not_book(
    booking_flow_context: tuple[
        ChatReceptionistService,
        TrackingAppointmentBookingService,
        FakeAppointmentHoldService,
        SchedulingService,
    ],
) -> None:
    service, tracking_booking, hold_service, scheduling = booking_flow_context
    appointments = scheduling.appointments
    assert isinstance(appointments, FakeAppointmentRepository)
    conversation = _conversation_with_active_hold(service)
    advance_new_patient_to_booking_summary(service, conversation)

    _expire_active_hold(hold_service)
    # The selected slot can no longer be held (taken in the meantime), so the
    # hold cannot be refreshed and the booking is unrecoverable.
    hold_service.create_hold_error = AppointmentSlotAlreadyHeldError(
        "slot already has an active hold",
    )

    result = service.handle_message(
        ChatMessageInput(message=FINAL_BOOKING_CONFIRM, conversation_id=conversation.id),
    )

    assert result.intent == ChatReceptionistIntent.BOOKING_HOLD_EXPIRED
    assert result.booking_confirmed is False
    # The slot can never be re-held, so booking is never completed.
    assert appointments.appointments == []
    assert len(tracking_booking.book_calls) == 1
    reply = result.reply.lower()
    assert "no longer available" in reply
    assert "choose another" in reply
    assert "has been booked" not in reply
    assert not _UUID_PATTERN.search(result.reply)

    chat_context = result.conversation.conversation_metadata["chat_context"]
    assert chat_context["hold_id"] is None
    assert chat_context["hold_expires_at"] is None
    assert chat_context["hold_owner_id"] is None
    assert chat_context["selected_availability_slot_id"] is None
    assert chat_context["selected_start_time"] is None
    assert chat_context.get("booking_identity_step") is None
    assert chat_context["appointment_intake_awaiting"] == "date_or_time_preference"


def test_new_scheduling_request_after_unrecoverable_hold_restarts_routing(
    booking_flow_context: tuple[
        ChatReceptionistService,
        TrackingAppointmentBookingService,
        FakeAppointmentHoldService,
        SchedulingService,
    ],
) -> None:
    service, _tracking_booking, hold_service, _scheduling = booking_flow_context
    conversation = _conversation_with_active_hold(service)
    advance_new_patient_to_booking_summary(service, conversation)

    _expire_active_hold(hold_service)
    hold_service.create_hold_error = AppointmentSlotAlreadyHeldError(
        "slot already has an active hold",
    )

    # Final confirmation lands on the unrecoverable cleanup path.
    service.handle_message(
        ChatMessageInput(message=FINAL_BOOKING_CONFIRM, conversation_id=conversation.id),
    )

    # A brand new scheduling request must restart routing, not loop on confirmation.
    result = service.handle_message(
        ChatMessageInput(
            message="I want to book a dermatologist appointment",
            conversation_id=conversation.id,
        ),
    )

    assert result.intent != ChatReceptionistIntent.BOOKING_CONFIRMATION_REQUIRED
    assert "please confirm if you would like me to book" not in result.reply.lower()


def test_repeated_confirmation_after_unrecoverable_hold_is_not_stuck(
    booking_flow_context: tuple[
        ChatReceptionistService,
        TrackingAppointmentBookingService,
        FakeAppointmentHoldService,
        SchedulingService,
    ],
) -> None:
    service, _tracking_booking, hold_service, _scheduling = booking_flow_context
    conversation = _conversation_with_active_hold(service)
    advance_new_patient_to_booking_summary(service, conversation)

    _expire_active_hold(hold_service)
    hold_service.create_hold_error = AppointmentSlotAlreadyHeldError(
        "slot already has an active hold",
    )

    service.handle_message(
        ChatMessageInput(message=FINAL_BOOKING_CONFIRM, conversation_id=conversation.id),
    )

    # Repeating the confirmation must not loop on the final confirmation prompt.
    result = service.handle_message(
        ChatMessageInput(message=FINAL_BOOKING_CONFIRM, conversation_id=conversation.id),
    )

    assert result.intent == ChatReceptionistIntent.BOOKING_HOLD_MISSING
    assert "please confirm if you would like me to book" not in result.reply.lower()


def test_new_request_overrides_stale_final_confirmation_with_missing_hold(
    booking_flow_context: tuple[
        ChatReceptionistService,
        TrackingAppointmentBookingService,
        FakeAppointmentHoldService,
        SchedulingService,
    ],
) -> None:
    service, tracking_booking, hold_service, _scheduling = booking_flow_context
    conversation = _conversation_with_active_hold(service)
    advance_new_patient_to_booking_summary(service, conversation)

    # The hold quietly expires while the context still sits at final confirmation.
    _expire_active_hold(hold_service)

    result = service.handle_message(
        ChatMessageInput(
            message="I want to book a dermatologist appointment",
            conversation_id=conversation.id,
        ),
    )

    assert result.intent != ChatReceptionistIntent.BOOKING_CONFIRMATION_REQUIRED
    assert "please confirm if you would like me to book" not in result.reply.lower()
    assert tracking_booking.book_calls == []
    assert not _UUID_PATTERN.search(result.reply)

    chat_context = result.conversation.conversation_metadata["chat_context"]
    assert chat_context["hold_id"] is None
    assert chat_context["selected_availability_slot_id"] is None
    assert chat_context.get("booking_identity_step") is None
