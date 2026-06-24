"""End-to-end voice identity resolution flows through the Retell tool adapter."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime
from typing import Any, cast
from uuid import uuid4

from sqlalchemy.orm import Session

from app.domain.conversations.enums import ConversationChannel, ConversationStatus
from app.domain.patient_identity_resolution import PatientResolutionNextStep
from app.domain.voice_calls.enums import VoiceCallStatus
from app.domain.voice_patient_intake import VoicePatientIntakeMode
from app.models.conversations import Conversation
from app.models.scheduling import Patient
from app.models.voice_calls import VoiceCall
from app.repositories.memory.patient_resolution import InMemoryPatientResolutionRepository
from app.schemas.retell_tools import RetellToolCallRequest
from app.services.appointment_booking import AppointmentBookingService
from app.services.audit_logs import AuditLogService
from app.services.conversations import ConversationService
from app.services.email_jobs import EmailJobService
from app.services.patient_identity_resolution import PatientIdentityResolutionService
from app.services.patient_intake import PatientIntakeService
from app.services.retell_tool_adapter import RetellToolCallingAdapter
from app.services.scheduling import SchedulingService
from app.services.voice_booking_confirmation import VoiceBookingConfirmationService
from app.services.voice_conversation_bridge import VoiceConversationBridgeService
from tests.clinic_time_test_support import make_test_clinic_time_service
from tests.test_appointment_booking_api import FakeAuditLogService, FakeDatabaseSession
from tests.test_appointment_booking_service import (
    FakePatientRepository,
    create_booking_context,
)
from tests.test_chat_receptionist_service import TrackingAppointmentBookingService
from tests.test_conversations import FakeConversationRepository
from tests.test_email_jobs import FakeEmailJobRepository
from tests.test_retell_call_lifecycle_service import FakeVoiceCallRepository
from tests.test_retell_tool_adapter import TrackingVoiceCallRepository
from tests.test_scheduling_services import FakeSpecialtyRepository
from tests.test_voice_booking_confirmation_service import FakeVoiceBookingAttemptRepository

PROVIDER_CALL_ID = "retell-call-identity-flow"
BOOK_TOOL_CALL_ID = "tool-call-book-flow"


@dataclass
class VoiceIdentityFlowContext:
    adapter: RetellToolCallingAdapter
    booking_context: Any
    tracking_booking: TrackingAppointmentBookingService
    patients: list[Patient]
    email_repository: FakeEmailJobRepository
    appointment_repository: Any
    conversation: Conversation


def create_voice_identity_flow_context(
    *,
    extra_patients: list[Patient] | None = None,
    patients_override: list[Patient] | None = None,
    intake_mode: VoicePatientIntakeMode = VoicePatientIntakeMode.DEMO_AUTO_CREATE,
) -> VoiceIdentityFlowContext:
    booking_context = create_booking_context()
    if patients_override is not None:
        repository_patients = patients_override
        booking_context.booking_service.patients = FakePatientRepository(repository_patients)
        patients = list(repository_patients)
    else:
        patients = [booking_context.patient]
        if extra_patients:
            repository_patients = [booking_context.patient, *extra_patients]
            booking_context.booking_service.patients = FakePatientRepository(repository_patients)
            patients = repository_patients

    hold = booking_context.hold_service.create_hold(
        availability_slot_id=booking_context.slot.id,
        doctor_id=booking_context.doctor.id,
        start_time=booking_context.slot.start_time,
        end_time=booking_context.slot.end_time,
        owner_id=PROVIDER_CALL_ID,
    )

    conversation_repository = FakeConversationRepository()
    conversation = Conversation(
        id=uuid4(),
        channel=ConversationChannel.VOICE,
        status=ConversationStatus.ACTIVE,
        external_conversation_id=PROVIDER_CALL_ID,
        call_id=PROVIDER_CALL_ID,
        conversation_metadata={
            "voice_context": {
                "hold_id": str(hold.hold_id),
                "availability_slot_id": str(booking_context.slot.id),
                "start_time": booking_context.slot.start_time.isoformat(),
                "end_time": booking_context.slot.end_time.isoformat(),
            },
        },
    )
    conversation_repository.conversations.append(conversation)

    voice_calls = FakeVoiceCallRepository()
    voice_call = VoiceCall(
        id=uuid4(),
        provider="retell",
        provider_call_id=PROVIDER_CALL_ID,
        status=VoiceCallStatus.IN_PROGRESS,
        conversation_id=conversation.id,
        created_at=datetime(2026, 6, 25, 10, 0, tzinfo=UTC),
        updated_at=datetime(2026, 6, 25, 10, 0, tzinfo=UTC),
    )
    voice_calls.voice_calls.append(voice_call)

    conversations = ConversationService(repository=conversation_repository)
    bridge = VoiceConversationBridgeService(
        voice_calls=voice_calls,
        conversations=conversation_repository,
        conversation_service=conversations,
    )

    inner_booking = booking_context.booking_service
    scheduling_service = SchedulingService(
        specialties=FakeSpecialtyRepository([]),
        doctors=inner_booking.doctors,
        patients=inner_booking.patients,
        availability_slots=inner_booking.availability_slots,
        appointments=inner_booking.appointments,
    )
    tracking_booking = TrackingAppointmentBookingService(inner_booking)
    resolution_repository = InMemoryPatientResolutionRepository()
    patient_intake = PatientIntakeService(
        patients=inner_booking.patients,
        mode=intake_mode,
    )
    patient_identity_resolution = PatientIdentityResolutionService(
        patients=inner_booking.patients,
        resolutions=resolution_repository,
        patient_intake=patient_intake,
    )

    email_repository = FakeEmailJobRepository()
    email_jobs = EmailJobService(repository=email_repository)
    audit_logs = FakeAuditLogService()
    db = FakeDatabaseSession()
    voice_booking_attempts = FakeVoiceBookingAttemptRepository()

    voice_booking_confirmation = VoiceBookingConfirmationService(
        db=cast(Session, db),
        booking_service=cast(AppointmentBookingService, tracking_booking),
        hold_service=booking_context.hold_service,
        scheduling_service=scheduling_service,
        conversations=conversations,
        audit_logs=cast(AuditLogService, audit_logs),
        email_jobs=email_jobs,
        voice_booking_attempts=voice_booking_attempts,
        appointments=booking_context.appointment_repository,
        availability_slots=inner_booking.availability_slots,
        patient_intake=patient_intake,
        patient_identity_resolution=patient_identity_resolution,
    )

    tracking_voice_calls = TrackingVoiceCallRepository()
    tracking_voice_calls.voice_calls[PROVIDER_CALL_ID] = voice_call

    adapter = RetellToolCallingAdapter(
        scheduling_service=scheduling_service,
        hold_service=booking_context.hold_service,
        voice_calls=tracking_voice_calls,
        voice_conversation_bridge=bridge,
        conversations=conversations,
        voice_booking_confirmation=voice_booking_confirmation,
        patient_identity_resolution=patient_identity_resolution,
        appointments=booking_context.appointment_repository,
        clinic_time_service=make_test_clinic_time_service(),
    )

    return VoiceIdentityFlowContext(
        adapter=adapter,
        booking_context=booking_context,
        tracking_booking=tracking_booking,
        patients=patients,
        email_repository=email_repository,
        appointment_repository=booking_context.appointment_repository,
        conversation=conversation,
    )


def _hold_id(flow: VoiceIdentityFlowContext) -> str:
    return str(next(iter(flow.booking_context.hold_repository.holds.values())).hold_id)


def _resolve_request(
    *,
    arguments: dict[str, Any],
    tool_call_id: str = "resolve-flow-1",
) -> RetellToolCallRequest:
    return RetellToolCallRequest.model_validate(
        {
            "provider_call_id": PROVIDER_CALL_ID,
            "tool_call_id": tool_call_id,
            "tool_name": "resolve_patient_identity",
            "arguments": arguments,
        },
    )


def _confirm_request(
    *,
    patient_resolution_id: str,
    confirmed: bool,
    tool_call_id: str = "confirm-flow-1",
) -> RetellToolCallRequest:
    return RetellToolCallRequest.model_validate(
        {
            "provider_call_id": PROVIDER_CALL_ID,
            "tool_call_id": tool_call_id,
            "tool_name": "confirm_patient_identity",
            "arguments": {
                "patient_resolution_id": patient_resolution_id,
                "confirmed": confirmed,
                "confirmation_text": "Yes, that's me." if confirmed else "No, that's not me.",
            },
        },
    )


def _book_request(
    *,
    patient_resolution_id: str,
    hold_id: str,
    slot_id: str,
    explicit_confirmation: bool = True,
    tool_call_id: str = BOOK_TOOL_CALL_ID,
    patient_name: str = "John Miller",
    patient_email: str = "john.miller@example.test",
) -> RetellToolCallRequest:
    return RetellToolCallRequest.model_validate(
        {
            "provider_call_id": PROVIDER_CALL_ID,
            "tool_call_id": tool_call_id,
            "tool_name": "book_appointment",
            "arguments": {
                "hold_id": hold_id,
                "slot_id": slot_id,
                "patient_name": patient_name,
                "patient_date_of_birth": "1985-04-12",
                "patient_email": patient_email,
                "patient_resolution_id": patient_resolution_id,
                "explicit_confirmation": explicit_confirmation,
                "confirmation_text": "Yes, please schedule that.",
            },
        },
    )


def test_existing_exact_patient_resolve_then_book_succeeds() -> None:
    flow = create_voice_identity_flow_context()
    hold_id = _hold_id(flow)
    slot_id = str(flow.booking_context.slot.id)

    resolve = flow.adapter.execute(
        _resolve_request(
            arguments={
                "patient_name": "John Miller",
                "patient_date_of_birth": "1985-04-12",
                "caller_claims_existing_patient": True,
                "allow_demo_patient_creation": False,
            },
        ),
    )

    assert resolve.status == "succeeded"
    assert resolve.result["match_status"] == "exact_match"
    resolution_id = resolve.result["patient_resolution_id"]
    assert resolution_id is not None

    book = flow.adapter.execute(
        _book_request(
            patient_resolution_id=resolution_id,
            hold_id=hold_id,
            slot_id=slot_id,
        ),
    )

    assert book.status == "succeeded"
    assert book.result["status"] == "scheduled"
    assert len(flow.tracking_booking.book_calls) == 1
    assert len(flow.appointment_repository.appointments) == 1


def test_possible_match_confirm_then_book_succeeds() -> None:
    michael = Patient(
        id=uuid4(),
        full_name="Michael Lee Reed",
        date_of_birth=date(1988, 3, 15),
        phone_number=None,
        email="michael.lee.reed@example.test",
    )
    flow = create_voice_identity_flow_context(extra_patients=[michael])
    hold_id = _hold_id(flow)
    slot_id = str(flow.booking_context.slot.id)

    resolve = flow.adapter.execute(
        _resolve_request(
            arguments={
                "patient_name": "Michael Reed",
                "patient_date_of_birth": "1988-03-15",
                "patient_email": "michael.lee.reed@example.test",
                "caller_claims_existing_patient": True,
                "allow_demo_patient_creation": False,
            },
            tool_call_id="resolve-possible-1",
        ),
    )

    assert resolve.status == "succeeded"
    assert resolve.result["match_status"] == "possible_match"
    assert resolve.result["requires_confirmation"] is True
    assert "Michael Lee Reed" in (resolve.result["confirmation_question"] or "")
    resolution_id = resolve.result["patient_resolution_id"]
    assert resolution_id is not None

    unconfirmed_book = flow.adapter.execute(
        _book_request(
            patient_resolution_id=resolution_id,
            hold_id=hold_id,
            slot_id=slot_id,
            patient_name="Michael Reed",
            patient_email="michael.lee.reed@example.test",
            tool_call_id="book-unconfirmed",
        ),
    )
    assert unconfirmed_book.status == "failed"
    assert unconfirmed_book.error_code == "patient_identity_confirmation_required"

    confirm = flow.adapter.execute(
        _confirm_request(
            patient_resolution_id=resolution_id,
            confirmed=True,
            tool_call_id="confirm-possible-1",
        ),
    )
    assert confirm.status == "succeeded"
    assert confirm.result["confirmed"] is True
    assert confirm.result["requires_confirmation"] is False

    book = flow.adapter.execute(
        _book_request(
            patient_resolution_id=resolution_id,
            hold_id=hold_id,
            slot_id=slot_id,
            patient_name="Michael Reed",
            patient_email="michael.lee.reed@example.test",
            tool_call_id="book-confirmed",
        ),
    )
    assert book.status == "succeeded"
    assert book.result["status"] == "scheduled"
    assert len(flow.tracking_booking.book_calls) == 1


def test_rejected_possible_match_prompts_identity_retry() -> None:
    michael = Patient(
        id=uuid4(),
        full_name="Michael Lee Reed",
        date_of_birth=date(1988, 3, 15),
        phone_number=None,
        email="michael.lee.reed@example.test",
    )
    flow = create_voice_identity_flow_context(extra_patients=[michael])

    resolve = flow.adapter.execute(
        _resolve_request(
            arguments={
                "patient_name": "Michael Reed",
                "patient_date_of_birth": "1988-03-15",
                "patient_email": "michael.lee.reed@example.test",
            },
            tool_call_id="resolve-reject-1",
        ),
    )
    resolution_id = resolve.result["patient_resolution_id"]

    reject = flow.adapter.execute(
        _confirm_request(
            patient_resolution_id=resolution_id,
            confirmed=False,
            tool_call_id="confirm-reject-1",
        ),
    )

    assert reject.status == "rejected"
    assert reject.error_code == "patient_identity_confirmation_rejected"
    assert reject.result["next_step"] == PatientResolutionNextStep.RETRY_IDENTITY.value
    lowered = reject.result["suggested_response_text"].lower()
    assert "email" in lowered or "phone" in lowered


def test_multiple_matches_prompts_for_email() -> None:
    shared_dob = date(1990, 7, 22)
    sarah_patients = [
        Patient(
            id=uuid4(),
            full_name="Sarah Chen",
            date_of_birth=shared_dob,
            phone_number="+1-555-0301",
            email="sarah.chen@example.test",
        ),
        Patient(
            id=uuid4(),
            full_name="Sarah Chen",
            date_of_birth=shared_dob,
            phone_number="+1-555-0302",
            email="sarah.chen.work@example.test",
        ),
    ]
    flow = create_voice_identity_flow_context(extra_patients=sarah_patients)

    resolve = flow.adapter.execute(
        _resolve_request(
            arguments={
                "patient_name": "Sarah Chen",
                "patient_date_of_birth": "1990-07-22",
                "caller_claims_existing_patient": True,
                "allow_demo_patient_creation": False,
            },
            tool_call_id="resolve-multiple-1",
        ),
    )

    assert resolve.status == "succeeded"
    assert resolve.result["match_status"] == "multiple_matches"
    assert resolve.result["next_step"] == PatientResolutionNextStep.COLLECT_EMAIL.value
    assert resolve.result["patient_resolution_id"] is None
    assert "email" in resolve.result["suggested_response_text"].lower()
    assert "sarah.chen@example.test" not in resolve.result["suggested_response_text"]

    narrowed = flow.adapter.execute(
        _resolve_request(
            arguments={
                "patient_name": "Sarah Chen",
                "patient_date_of_birth": "1990-07-22",
                "patient_email": "sarah.chen@example.test",
                "caller_claims_existing_patient": True,
                "allow_demo_patient_creation": False,
            },
            tool_call_id="resolve-multiple-2",
        ),
    )
    assert narrowed.status == "succeeded"
    assert narrowed.result["match_status"] == "exact_match"
    assert narrowed.result["patient_resolution_id"] is not None


def test_new_patient_resolve_created_then_book_succeeds() -> None:
    flow = create_voice_identity_flow_context()
    hold_id = _hold_id(flow)
    slot_id = str(flow.booking_context.slot.id)

    resolve = flow.adapter.execute(
        _resolve_request(
            arguments={
                "patient_name": "Felipe Logan",
                "patient_date_of_birth": "1995-11-02",
                "patient_email": "felipe.logan@example.test",
                "caller_claims_existing_patient": False,
                "allow_demo_patient_creation": True,
            },
            tool_call_id="resolve-new-1",
        ),
    )

    assert resolve.status == "succeeded"
    assert resolve.result["match_status"] == "created"
    resolution_id = resolve.result["patient_resolution_id"]
    assert resolution_id is not None

    book = flow.adapter.execute(
        _book_request(
            patient_resolution_id=resolution_id,
            hold_id=hold_id,
            slot_id=slot_id,
            patient_name="Felipe Logan",
            patient_email="felipe.logan@example.test",
            tool_call_id="book-new-1",
        ),
    )

    assert book.status == "succeeded"
    assert book.result["status"] == "scheduled"
    assert len(flow.tracking_booking.book_calls) == 1
    repository = cast(FakePatientRepository, flow.booking_context.booking_service.patients)
    created = [
        patient
        for patient in repository.patients
        if patient.email == "felipe.logan@example.test"
    ]
    assert len(created) == 1
    assert created[0].phone_number is None


def test_invented_email_guard_rejects_booking_before_caller_confirms_email() -> None:
    """Agent must not book with invented email; inline fallback requires real match."""
    flow = create_voice_identity_flow_context(
        patients_override=[],
        intake_mode=VoicePatientIntakeMode.LOOKUP_ONLY,
    )
    hold_id = _hold_id(flow)
    slot_id = str(flow.booking_context.slot.id)

    premature_book = flow.adapter.execute(
        RetellToolCallRequest.model_validate(
            {
                "provider_call_id": PROVIDER_CALL_ID,
                "tool_call_id": "book-invented-email",
                "tool_name": "book_appointment",
                "arguments": {
                    "hold_id": hold_id,
                    "slot_id": slot_id,
                    "patient_name": "Felipe Logan",
                    "patient_date_of_birth": "1995-11-02",
                    "patient_email": "felipe.logan@example.test",
                    "explicit_confirmation": True,
                },
            },
        ),
    )

    assert premature_book.status == "failed"
    assert premature_book.error_code == "patient_not_found"
    assert len(flow.tracking_booking.book_calls) == 0


def test_explicit_confirmation_still_required_with_resolution_token() -> None:
    flow = create_voice_identity_flow_context()
    hold_id = _hold_id(flow)
    slot_id = str(flow.booking_context.slot.id)

    resolve = flow.adapter.execute(
        _resolve_request(
            arguments={
                "patient_name": "John Miller",
                "patient_date_of_birth": "1985-04-12",
            },
        ),
    )
    resolution_id = resolve.result["patient_resolution_id"]

    book = flow.adapter.execute(
        _book_request(
            patient_resolution_id=resolution_id,
            hold_id=hold_id,
            slot_id=slot_id,
            explicit_confirmation=False,
            tool_call_id="book-no-confirm",
        ),
    )

    assert book.status == "failed"
    assert book.error_code == "booking_confirmation_required"
    assert len(flow.tracking_booking.book_calls) == 0


def test_hold_still_required_with_resolution_token() -> None:
    flow = create_voice_identity_flow_context()
    slot_id = str(flow.booking_context.slot.id)
    hold = next(iter(flow.booking_context.hold_repository.holds.values()))
    flow.booking_context.hold_repository.delete(
        doctor_id=hold.doctor_id,
        start_time=hold.start_time,
    )

    resolve = flow.adapter.execute(
        _resolve_request(
            arguments={
                "patient_name": "John Miller",
                "patient_date_of_birth": "1985-04-12",
            },
        ),
    )
    resolution_id = resolve.result["patient_resolution_id"]

    book = flow.adapter.execute(
        _book_request(
            patient_resolution_id=resolution_id,
            hold_id=str(hold.hold_id),
            slot_id=slot_id,
            tool_call_id="book-no-hold",
        ),
    )

    assert book.status == "failed"
    assert book.error_code == "appointment_hold_expired"
    assert len(flow.tracking_booking.book_calls) == 0


def test_duplicate_booking_retry_with_resolution_token_is_idempotent() -> None:
    flow = create_voice_identity_flow_context()
    hold_id = _hold_id(flow)
    slot_id = str(flow.booking_context.slot.id)

    resolve = flow.adapter.execute(
        _resolve_request(
            arguments={
                "patient_name": "John Miller",
                "patient_date_of_birth": "1985-04-12",
            },
        ),
    )
    resolution_id = resolve.result["patient_resolution_id"]
    request = _book_request(
        patient_resolution_id=resolution_id,
        hold_id=hold_id,
        slot_id=slot_id,
    )

    first = flow.adapter.execute(request)
    second = flow.adapter.execute(request)

    assert first.status == "succeeded"
    assert second.status == "succeeded"
    assert second.duplicate is True
    assert len(flow.tracking_booking.book_calls) == 1
    assert len(flow.email_repository.email_jobs) == 1
    assert len(flow.appointment_repository.appointments) == 1


def test_fallback_inline_identity_booking_without_resolution_token() -> None:
    flow = create_voice_identity_flow_context()

    book = flow.adapter.execute(
        RetellToolCallRequest.model_validate(
            {
                "provider_call_id": PROVIDER_CALL_ID,
                "tool_call_id": "book-fallback",
                "tool_name": "book_appointment",
                "arguments": {
                    "hold_id": _hold_id(flow),
                    "slot_id": str(flow.booking_context.slot.id),
                    "patient_name": "John Miller",
                    "patient_date_of_birth": "1985-04-12",
                    "patient_email": "john.miller@example.test",
                    "explicit_confirmation": True,
                },
            },
        ),
    )

    assert book.status == "succeeded"
    assert book.result["status"] == "scheduled"
