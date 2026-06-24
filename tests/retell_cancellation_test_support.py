from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any, cast
from uuid import uuid4

from app.domain.appointments import (
    AppointmentCancellationRequest,
    AppointmentCancellationResult,
)
from app.domain.conversations.enums import ConversationChannel, ConversationStatus
from app.domain.patient_identity_resolution import PatientIdentityResolutionRequest
from app.domain.scheduling.enums import AppointmentStatus, AvailabilitySlotStatus
from app.domain.voice_calls.enums import VoiceCallStatus
from app.domain.voice_patient_intake import VoicePatientIntakeMode
from app.models.conversations import Conversation
from app.models.scheduling import Appointment, AvailabilitySlot, Doctor, Patient, Specialty
from app.models.voice_calls import VoiceCall
from app.repositories.memory.patient_resolution import InMemoryPatientResolutionRepository
from app.schemas.retell_tools import RetellToolCallRequest
from app.services.appointment_cancellation import AppointmentCancellationService
from app.services.conversations import ConversationService
from app.services.patient_identity_resolution import PatientIdentityResolutionService
from app.services.patient_intake import PatientIntakeService
from app.services.retell_tool_adapter import RetellToolCallingAdapter
from app.services.scheduling import SchedulingService
from app.services.voice_appointment_cancellation import VoiceAppointmentCancellationService
from app.services.voice_conversation_bridge import VoiceConversationBridgeService
from app.services.voice_patient_appointment_lookup import VoicePatientAppointmentLookupService
from tests.clinic_time_test_support import make_test_clinic_time_service
from tests.test_appointment_booking_service import (
    FakeAppointmentRepository,
    FakeAvailabilitySlotRepository,
    FakeDoctorRepository,
    create_booking_context,
)
from tests.test_appointment_cancellation_service import create_cancellation_context
from tests.test_conversations import FakeConversationRepository
from tests.test_patient_identity_resolution_service import FakePatientRepository
from tests.test_retell_call_lifecycle_service import FakeVoiceCallRepository
from tests.test_retell_tool_adapter import TrackingVoiceCallRepository
from tests.test_scheduling_services import FakeSpecialtyRepository

PROVIDER_CALL_ID = "retell-call-cancel-1"
TOOL_CALL_ID = "tool-call-cancel-1"


class TrackingAppointmentCancellationService:
    def __init__(self, inner: AppointmentCancellationService) -> None:
        self.inner = inner
        self.cancel_calls: list[AppointmentCancellationRequest] = []

    def cancel_appointment(
        self,
        request: AppointmentCancellationRequest,
    ) -> AppointmentCancellationResult:
        self.cancel_calls.append(request)
        return self.inner.cancel_appointment(request)


def cancellation_arguments(
    *,
    appointment_id: str | None = None,
    patient_resolution_id: str | None = None,
    explicit_confirmation: bool = True,
    confirmation_text: str = "yes, cancel it",
    tool_call_id: str = TOOL_CALL_ID,
) -> dict[str, object]:
    payload: dict[str, object] = {
        "explicit_confirmation": explicit_confirmation,
        "confirmation_text": confirmation_text,
        "cancellation_reason": "Patient requested cancellation",
    }
    if appointment_id is not None:
        payload["appointment_id"] = appointment_id
    if patient_resolution_id is not None:
        payload["patient_resolution_id"] = patient_resolution_id
    return payload


def cancellation_tool_request(
    *,
    appointment_id: str | None = None,
    patient_resolution_id: str | None = None,
    explicit_confirmation: bool = True,
    confirmation_text: str = "yes, cancel it",
    tool_call_id: str = TOOL_CALL_ID,
) -> RetellToolCallRequest:
    return RetellToolCallRequest.model_validate(
        {
            "provider_call_id": PROVIDER_CALL_ID,
            "tool_call_id": tool_call_id,
            "tool_name": "cancel_appointment",
            "arguments": cancellation_arguments(
                appointment_id=appointment_id,
                patient_resolution_id=patient_resolution_id,
                explicit_confirmation=explicit_confirmation,
                confirmation_text=confirmation_text,
                tool_call_id=tool_call_id,
            ),
        },
    )


def create_retell_cancellation_tool_context(
    *,
    status: AppointmentStatus = AppointmentStatus.SCHEDULED,
    start_time: datetime | None = None,
    availability_slot: AvailabilitySlot | None = None,
    extra_patients: list[Patient] | None = None,
) -> dict[str, Any]:
    clinic_time_service = make_test_clinic_time_service()
    if start_time is None:
        start_time = clinic_time_service.clinic_now() + timedelta(days=7)

    specialty = Specialty(
        id=uuid4(),
        name="Dermatology",
        description="Skin care",
        is_active=True,
    )
    doctor = Doctor(
        id=uuid4(),
        specialty_id=specialty.id,
        full_name="Dr. Emily Carter",
        email="emily.carter@example-clinic.test",
        phone_number="+1-555-0101",
        is_active=True,
    )
    patient = Patient(
        id=uuid4(),
        full_name="John Miller",
        date_of_birth=datetime(1985, 4, 12).date(),
        phone_number="+1-555-0201",
        email="john.miller@example.test",
    )
    patients = [patient, *(extra_patients or [])]

    slot = availability_slot
    if slot is None:
        slot = AvailabilitySlot(
            id=uuid4(),
            doctor_id=doctor.id,
            start_time=start_time,
            end_time=start_time + timedelta(minutes=30),
            status=AvailabilitySlotStatus.BOOKED,
        )

    appointment = Appointment(
        id=uuid4(),
        patient_id=patient.id,
        doctor_id=doctor.id,
        specialty_id=specialty.id,
        availability_slot_id=slot.id,
        start_time=start_time,
        end_time=start_time + timedelta(minutes=30),
        status=status,
    )
    if status == AppointmentStatus.CANCELLED:
        appointment.cancelled_at = datetime(2026, 6, 20, 9, 0, tzinfo=UTC)

    appointment_repository = FakeAppointmentRepository([appointment])
    availability_repository = FakeAvailabilitySlotRepository([slot])
    patient_repository = FakePatientRepository(patients)
    cancellation_context = create_cancellation_context(status=status)
    cancellation_context.appointment_repository.appointments = [appointment]
    cancellation_context.service = AppointmentCancellationService(
        appointments=appointment_repository,
        cancellation_attempts=cancellation_context.attempt_repository,
        audit_logs=cast(Any, cancellation_context.audit_logs),
        availability_slots=availability_repository,
    )

    booking_context = create_booking_context()

    conversation_repository = FakeConversationRepository()
    conversation = Conversation(
        id=uuid4(),
        channel=ConversationChannel.VOICE,
        status=ConversationStatus.ACTIVE,
        external_conversation_id=PROVIDER_CALL_ID,
        call_id=PROVIDER_CALL_ID,
        appointment_id=appointment.id,
        conversation_metadata={"voice_context": {}},
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

    tracking_cancellation = TrackingAppointmentCancellationService(
        cancellation_context.service,
    )

    resolution_repository = InMemoryPatientResolutionRepository()
    patient_identity_resolution = PatientIdentityResolutionService(
        patients=patient_repository,
        resolutions=resolution_repository,
        patient_intake=PatientIntakeService(
            patients=patient_repository,
            mode=VoicePatientIntakeMode.LOOKUP_ONLY,
        ),
    )
    resolution_result = patient_identity_resolution.resolve(
        PatientIdentityResolutionRequest(
            patient_name=patient.full_name,
            patient_date_of_birth=patient.date_of_birth,
            provider_call_id=PROVIDER_CALL_ID,
            conversation_id=conversation.id,
            patient_email=patient.email,
        ),
    )
    patient_resolution_id = resolution_result.patient_resolution_id
    assert patient_resolution_id is not None

    scheduling_service = SchedulingService(
        specialties=FakeSpecialtyRepository([specialty]),
        doctors=FakeDoctorRepository([doctor]),
        patients=patient_repository,
        availability_slots=availability_repository,
        appointments=appointment_repository,
    )
    voice_appointment_cancellation = VoiceAppointmentCancellationService(
        patient_identity_resolution=patient_identity_resolution,
        appointments=appointment_repository,
        appointment_cancellation=cast(AppointmentCancellationService, tracking_cancellation),
        scheduling_metadata=scheduling_service,
        clinic_time_service=clinic_time_service,
    )
    voice_patient_appointment_lookup = VoicePatientAppointmentLookupService(
        patient_identity_resolution=patient_identity_resolution,
        appointments=appointment_repository,
        scheduling_metadata=scheduling_service,
        clinic_time_service=clinic_time_service,
    )

    tracking_voice_calls = TrackingVoiceCallRepository()
    tracking_voice_calls.voice_calls[PROVIDER_CALL_ID] = voice_call

    adapter = RetellToolCallingAdapter(
        scheduling_service=scheduling_service,
        hold_service=booking_context.hold_service,
        voice_calls=tracking_voice_calls,
        voice_conversation_bridge=bridge,
        conversations=conversations,
        appointment_cancellation=tracking_cancellation,
        appointments=appointment_repository,
        clinic_time_service=clinic_time_service,
        patient_identity_resolution=patient_identity_resolution,
        voice_patient_appointment_lookup=voice_patient_appointment_lookup,
        voice_appointment_cancellation=voice_appointment_cancellation,
    )

    return {
        "adapter": adapter,
        "tracking_cancellation": tracking_cancellation,
        "cancellation_context": cancellation_context,
        "conversation": conversation,
        "conversation_repository": conversation_repository,
        "appointment": appointment,
        "patient": patient,
        "patient_resolution_id": patient_resolution_id,
        "slot": slot,
        "availability_repository": availability_repository,
        "patient_identity_resolution": patient_identity_resolution,
        "appointment_repository": appointment_repository,
        "clinic_time_service": clinic_time_service,
        "specialty": specialty,
        "doctor": doctor,
    }
