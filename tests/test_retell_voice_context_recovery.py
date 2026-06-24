from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any, cast

import pytest
from pydantic import ValidationError
from sqlalchemy.orm import Session

from app.domain.conversations.enums import ConversationChannel
from app.domain.voice_calls.enums import VoiceCallStatus
from app.domain.voice_conversation import read_voice_context
from app.models.scheduling import Specialty
from app.schemas.retell_tools import BookAppointmentToolArguments, RetellToolCallRequest
from app.services.appointment_booking import AppointmentBookingService
from app.services.audit_logs import AuditLogService
from app.services.conversations import ConversationService
from app.services.email_jobs import EmailJobService
from app.services.retell_call_lifecycle import (
    RetellCallLifecycleService,
    RetellLifecyclePayload,
)
from app.services.retell_tool_adapter import RetellToolCallingAdapter
from app.services.scheduling import SchedulingService
from app.services.voice_booking_confirmation import VoiceBookingConfirmationService
from app.services.voice_conversation_bridge import VoiceConversationBridgeService
from tests.clinic_time_test_support import make_test_clinic_time_service
from tests.test_appointment_booking_api import FakeAuditLogService, FakeDatabaseSession
from tests.test_appointment_booking_service import create_booking_context
from tests.test_chat_receptionist_service import TrackingAppointmentBookingService
from tests.test_conversations import FakeConversationRepository
from tests.test_email_jobs import FakeEmailJobRepository
from tests.test_retell_call_lifecycle_service import FakeVoiceCallRepository
from tests.test_retell_tool_adapter import TrackingSchedulingService
from tests.test_scheduling_services import FakeSpecialtyRepository
from tests.test_voice_booking_confirmation_service import FakeVoiceBookingAttemptRepository

PROVIDER_CALL_ID = "retell-call-lazy-context"
HOLD_TOOL_CALL_ID = "hold-lazy-1"
BOOK_TOOL_CALL_ID = "book-lazy-1"


def _create_lazy_voice_booking_bundle() -> dict[str, Any]:
    booking_context = create_booking_context()
    slot_start = datetime(2026, 7, 1, 14, 0, tzinfo=UTC)
    booking_context.slot.start_time = slot_start
    booking_context.slot.end_time = slot_start + timedelta(minutes=30)
    specialty = Specialty(
        id=booking_context.doctor.specialty_id,
        name="Dermatology",
        description="Skin care",
        is_active=True,
    )
    scheduling_service = TrackingSchedulingService(
        specialties=[specialty],
        doctors=[booking_context.doctor],
        availability_slots=[booking_context.slot],
    )

    hold_service = booking_context.hold_service
    voice_calls = FakeVoiceCallRepository()
    conversation_repository = FakeConversationRepository()
    conversations = ConversationService(repository=conversation_repository)
    bridge = VoiceConversationBridgeService(
        voice_calls=voice_calls,
        conversations=conversation_repository,
        conversation_service=conversations,
    )

    inner_booking = booking_context.booking_service
    tracking_booking = TrackingAppointmentBookingService(inner_booking)
    email_repository = FakeEmailJobRepository()
    email_jobs = EmailJobService(repository=email_repository)
    audit_logs = FakeAuditLogService()
    db = FakeDatabaseSession()
    voice_booking_attempts = FakeVoiceBookingAttemptRepository()

    voice_booking_confirmation = VoiceBookingConfirmationService(
        db=cast(Session, db),
        booking_service=cast(AppointmentBookingService, tracking_booking),
        hold_service=hold_service,
        scheduling_service=SchedulingService(
            specialties=FakeSpecialtyRepository([]),
            doctors=inner_booking.doctors,
            patients=inner_booking.patients,
            availability_slots=inner_booking.availability_slots,
            appointments=inner_booking.appointments,
        ),
        conversations=conversations,
        audit_logs=cast(AuditLogService, audit_logs),
        email_jobs=email_jobs,
        voice_booking_attempts=voice_booking_attempts,
        appointments=booking_context.appointment_repository,
        availability_slots=inner_booking.availability_slots,
    )

    adapter = RetellToolCallingAdapter(
        scheduling_service=scheduling_service,
        hold_service=hold_service,
        voice_calls=voice_calls,
        voice_conversation_bridge=bridge,
        conversations=conversations,
        voice_booking_confirmation=voice_booking_confirmation,
        appointments=booking_context.appointment_repository,
        clinic_time_service=make_test_clinic_time_service(),
    )

    lifecycle_service = RetellCallLifecycleService(repository=voice_calls)

    return {
        "adapter": adapter,
        "booking_context": booking_context,
        "scheduling_service": scheduling_service,
        "hold_service": hold_service,
        "voice_calls": voice_calls,
        "conversation_repository": conversation_repository,
        "tracking_booking": tracking_booking,
        "lifecycle_service": lifecycle_service,
        "email_repository": email_repository,
        "appointment_repository": booking_context.appointment_repository,
    }


def _booking_arguments(
    *,
    hold_id: str,
    slot_id: str,
    explicit_confirmation: bool = True,
    patient_email: str = "john.miller@example.test",
) -> dict[str, object]:
    return {
        "hold_id": hold_id,
        "slot_id": slot_id,
        "patient_name": "John Miller",
        "patient_date_of_birth": "1985-04-12",
        "patient_email": patient_email,
        "patient_phone": "+1-555-0201",
        "explicit_confirmation": explicit_confirmation,
        "confirmation_text": "Yes, please book it.",
    }


def test_tool_flow_without_prior_lifecycle_creates_context_and_books() -> None:
    bundle = _create_lazy_voice_booking_bundle()
    adapter = bundle["adapter"]
    booking_context = bundle["booking_context"]
    slot_id = str(booking_context.slot.id)

    check_response = adapter.execute(
        RetellToolCallRequest.model_validate(
            {
                "provider_call_id": PROVIDER_CALL_ID,
                "tool_name": "check_availability",
                "arguments": {
                    "doctor_id": str(booking_context.doctor.id),
                    "date_expression": {
                        "kind": "exact_date",
                        "exact_date": booking_context.slot.start_time.date().isoformat(),
                    },
                },
            },
        ),
    )
    assert check_response.status == "succeeded"

    hold_response = adapter.execute(
        RetellToolCallRequest.model_validate(
            {
                "provider_call_id": PROVIDER_CALL_ID,
                "tool_call_id": HOLD_TOOL_CALL_ID,
                "tool_name": "hold_appointment_slot",
                "arguments": {"availability_slot_id": slot_id},
            },
        ),
    )
    assert hold_response.status == "succeeded"
    hold_id = hold_response.result["hold_id"]

    assert len(bundle["voice_calls"].voice_calls) == 1
    assert len(bundle["conversation_repository"].conversations) == 1

    book_response = adapter.execute(
        RetellToolCallRequest.model_validate(
            {
                "provider_call_id": PROVIDER_CALL_ID,
                "tool_call_id": BOOK_TOOL_CALL_ID,
                "tool_name": "book_appointment",
                "arguments": _booking_arguments(hold_id=hold_id, slot_id=slot_id),
            },
        ),
    )

    assert book_response.status == "succeeded"
    assert book_response.result["status"] == "scheduled"
    assert len(bundle["tracking_booking"].book_calls) == 1


def test_lifecycle_first_flow_still_works() -> None:
    bundle = _create_lazy_voice_booking_bundle()
    occurred_at = datetime(2026, 6, 23, 12, 0, tzinfo=UTC)

    bundle["lifecycle_service"].ingest_event(
        RetellLifecyclePayload(
            provider_call_id=PROVIDER_CALL_ID,
            event_type="call_started",
            occurred_at=occurred_at,
        ),
    )

    slot_id = str(bundle["booking_context"].slot.id)
    hold_response = bundle["adapter"].execute(
        RetellToolCallRequest.model_validate(
            {
                "provider_call_id": PROVIDER_CALL_ID,
                "tool_call_id": "hold-life-first",
                "tool_name": "hold_appointment_slot",
                "arguments": {"availability_slot_id": slot_id},
            },
        ),
    )
    assert hold_response.status == "succeeded"

    book_response = bundle["adapter"].execute(
        RetellToolCallRequest.model_validate(
            {
                "provider_call_id": PROVIDER_CALL_ID,
                "tool_call_id": "book-life-first",
                "tool_name": "book_appointment",
                "arguments": _booking_arguments(
                    hold_id=hold_response.result["hold_id"],
                    slot_id=slot_id,
                ),
            },
        ),
    )

    assert book_response.status == "succeeded"
    assert len(bundle["voice_calls"].voice_calls) == 1


def test_lifecycle_after_tools_reuses_existing_voice_call() -> None:
    bundle = _create_lazy_voice_booking_bundle()
    adapter = bundle["adapter"]
    slot_id = str(bundle["booking_context"].slot.id)

    hold_response = adapter.execute(
        RetellToolCallRequest.model_validate(
            {
                "provider_call_id": PROVIDER_CALL_ID,
                "tool_call_id": "hold-before-life",
                "tool_name": "hold_appointment_slot",
                "arguments": {"availability_slot_id": slot_id},
            },
        ),
    )
    assert hold_response.status == "succeeded"

    voice_call_before = bundle["voice_calls"].voice_calls[0]
    conversation_before = bundle["conversation_repository"].conversations[0]

    bundle["lifecycle_service"].ingest_event(
        RetellLifecyclePayload(
            provider_call_id=PROVIDER_CALL_ID,
            event_type="call_started",
            occurred_at=datetime(2026, 6, 23, 12, 0, tzinfo=UTC),
        ),
    )

    assert len(bundle["voice_calls"].voice_calls) == 1
    voice_call_after = bundle["voice_calls"].voice_calls[0]
    assert voice_call_after.id == voice_call_before.id
    assert voice_call_after.conversation_id == conversation_before.id
    assert voice_call_after.status == VoiceCallStatus.IN_PROGRESS


def test_hold_without_prior_voice_call_persists_conversation_metadata() -> None:
    bundle = _create_lazy_voice_booking_bundle()
    slot_id = str(bundle["booking_context"].slot.id)

    response = bundle["adapter"].execute(
        RetellToolCallRequest.model_validate(
            {
                "provider_call_id": PROVIDER_CALL_ID,
                "tool_call_id": "hold-context-lazy",
                "tool_name": "hold_appointment_slot",
                "arguments": {"availability_slot_id": slot_id},
            },
        ),
    )

    assert response.status == "succeeded"
    assert len(bundle["voice_calls"].voice_calls) == 1
    conversation = bundle["conversation_repository"].conversations[0]
    assert conversation.channel == ConversationChannel.VOICE
    voice_context = read_voice_context(conversation.conversation_metadata)
    assert voice_context["hold_id"] == response.result["hold_id"]
    assert voice_context["availability_slot_id"] == slot_id


def test_book_appointment_rejects_missing_explicit_confirmation() -> None:
    bundle = _create_lazy_voice_booking_bundle()
    slot_id = str(bundle["booking_context"].slot.id)

    hold_response = bundle["adapter"].execute(
        RetellToolCallRequest.model_validate(
            {
                "provider_call_id": PROVIDER_CALL_ID,
                "tool_call_id": "hold-no-confirm",
                "tool_name": "hold_appointment_slot",
                "arguments": {"availability_slot_id": slot_id},
            },
        ),
    )
    hold_id = hold_response.result["hold_id"]

    response = bundle["adapter"].execute(
        RetellToolCallRequest.model_validate(
            {
                "provider_call_id": PROVIDER_CALL_ID,
                "tool_call_id": "book-no-confirm",
                "tool_name": "book_appointment",
                "arguments": _booking_arguments(
                    hold_id=hold_id,
                    slot_id=slot_id,
                    explicit_confirmation=False,
                ),
            },
        ),
    )

    assert response.status == "failed"
    assert response.error_code == "booking_confirmation_required"
    assert bundle["tracking_booking"].book_calls == []


def test_book_appointment_rejects_expired_hold() -> None:
    bundle = _create_lazy_voice_booking_bundle()
    slot_id = str(bundle["booking_context"].slot.id)

    hold_response = bundle["adapter"].execute(
        RetellToolCallRequest.model_validate(
            {
                "provider_call_id": PROVIDER_CALL_ID,
                "tool_call_id": "hold-expired",
                "tool_name": "hold_appointment_slot",
                "arguments": {"availability_slot_id": slot_id},
            },
        ),
    )
    hold = next(iter(bundle["booking_context"].hold_repository.holds.values()))
    bundle["booking_context"].hold_repository.delete(
        doctor_id=hold.doctor_id,
        start_time=hold.start_time,
    )

    response = bundle["adapter"].execute(
        RetellToolCallRequest.model_validate(
            {
                "provider_call_id": PROVIDER_CALL_ID,
                "tool_call_id": "book-expired",
                "tool_name": "book_appointment",
                "arguments": _booking_arguments(
                    hold_id=hold_response.result["hold_id"],
                    slot_id=slot_id,
                ),
            },
        ),
    )

    assert response.status == "failed"
    assert response.error_code == "appointment_hold_expired"


def test_book_appointment_rejects_invalid_patient_email() -> None:
    bundle = _create_lazy_voice_booking_bundle()
    slot_id = str(bundle["booking_context"].slot.id)

    hold_response = bundle["adapter"].execute(
        RetellToolCallRequest.model_validate(
            {
                "provider_call_id": PROVIDER_CALL_ID,
                "tool_call_id": "hold-bad-email",
                "tool_name": "hold_appointment_slot",
                "arguments": {"availability_slot_id": slot_id},
            },
        ),
    )

    with pytest.raises(ValidationError):
        BookAppointmentToolArguments.model_validate(
            {
                **_booking_arguments(
                    hold_id=hold_response.result["hold_id"],
                    slot_id=slot_id,
                ),
                "patient_email": "not-an-email",
            },
        )


def test_ensure_conversation_for_tool_callback_creates_voice_call_when_missing(
    bridge_bundle: tuple[
        VoiceConversationBridgeService,
        FakeVoiceCallRepository,
        FakeConversationRepository,
    ],
) -> None:
    service, voice_calls, conversations = bridge_bundle

    conversation = service.ensure_conversation_for_tool_callback("retell", "call-lazy-create")

    assert len(voice_calls.voice_calls) == 1
    assert voice_calls.voice_calls[0].provider_call_id == "call-lazy-create"
    assert len(conversations.conversations) == 1
    assert voice_calls.voice_calls[0].conversation_id == conversation.id


@pytest.fixture()
def bridge_bundle() -> tuple[
    VoiceConversationBridgeService,
    FakeVoiceCallRepository,
    FakeConversationRepository,
]:
    voice_calls = FakeVoiceCallRepository()
    conversations = FakeConversationRepository()
    service = VoiceConversationBridgeService(
        voice_calls=voice_calls,
        conversations=conversations,
        conversation_service=ConversationService(repository=conversations),
    )

    return service, voice_calls, conversations
