from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, cast
from uuid import uuid4

from app.domain.appointment_rescheduling import (
    AppointmentReschedulingRequest,
    AppointmentReschedulingResult,
)
from app.domain.conversations.enums import ConversationChannel, ConversationStatus
from app.domain.patient_identity_resolution import PatientIdentityResolutionRequest
from app.domain.scheduling.enums import AppointmentStatus, AvailabilitySlotStatus
from app.domain.voice_calls.enums import VoiceCallStatus
from app.domain.voice_patient_intake import VoicePatientIntakeMode
from app.models.conversations import Conversation
from app.models.voice_calls import VoiceCall
from app.repositories.memory.patient_resolution import InMemoryPatientResolutionRepository
from app.schemas.retell_tools import RetellToolCallRequest
from app.services.appointment_rescheduling import AppointmentReschedulingService
from app.services.patient_identity_resolution import PatientIdentityResolutionService
from app.services.patient_intake import PatientIntakeService
from app.services.retell_tool_adapter import RetellToolCallingAdapter
from app.services.scheduling import SchedulingService
from app.services.voice_appointment_rescheduling import VoiceAppointmentReschedulingService
from app.services.voice_conversation_bridge import VoiceConversationBridgeService
from app.services.voice_patient_appointment_lookup import VoicePatientAppointmentLookupService
from tests.clinic_time_test_support import make_test_clinic_time_service
from tests.test_appointment_booking_service import FakePatientRepository
from tests.test_appointment_rescheduling_service import create_rescheduling_context
from tests.test_conversations import FakeConversationRepository
from tests.test_patient_identity_resolution_service import (
    FakePatientRepository as LookupPatientRepo,
)
from tests.test_retell_call_lifecycle_service import FakeVoiceCallRepository
from tests.test_retell_tool_adapter import TrackingVoiceCallRepository
from tests.test_scheduling_services import FakeSpecialtyRepository

PROVIDER_CALL_ID = "retell-call-reschedule-1"
TOOL_CALL_ID = "tool-call-reschedule-1"


class TrackingAppointmentReschedulingService:
    def __init__(self, inner: AppointmentReschedulingService) -> None:
        self.inner = inner
        self.reschedule_calls: list[AppointmentReschedulingRequest] = []

    def reschedule_appointment(
        self,
        request: AppointmentReschedulingRequest,
    ) -> AppointmentReschedulingResult:
        self.reschedule_calls.append(request)
        return self.inner.reschedule_appointment(request)


def reschedule_arguments(
    *,
    appointment_id: str | None = None,
    original_appointment_id: str | None = None,
    patient_resolution_id: str | None = None,
    hold_id: str | None = None,
    new_slot_id: str | None = None,
    explicit_confirmation: bool = True,
    confirmation_text: str | None = "Yes, please reschedule it.",
    reschedule_reason: str | None = "Patient requested a new time",
) -> dict[str, object]:
    payload: dict[str, object] = {
        "explicit_confirmation": explicit_confirmation,
        "confirmation_text": confirmation_text,
        "reschedule_reason": reschedule_reason,
    }
    resolved_appointment_id = appointment_id or original_appointment_id
    if resolved_appointment_id is not None:
        payload["appointment_id"] = resolved_appointment_id
    if patient_resolution_id is not None:
        payload["patient_resolution_id"] = patient_resolution_id
    if hold_id is not None:
        payload["hold_id"] = hold_id
    if new_slot_id is not None:
        payload["new_slot_id"] = new_slot_id
    return payload


def valid_reschedule_arguments(
    *,
    appointment_id: str | None = None,
    original_appointment_id: str | None = None,
    patient_resolution_id: str | None = None,
    hold_id: str | None = None,
    new_slot_id: str | None = None,
    explicit_confirmation: bool = True,
    confirmation_text: str | None = "Yes, please reschedule it.",
    reschedule_reason: str | None = "Patient requested a new time",
) -> dict[str, object]:
    resolved_hold_id = hold_id if hold_id is not None else str(uuid4())
    resolved_slot_id = new_slot_id if new_slot_id is not None else str(uuid4())
    resolved_patient_resolution_id = (
        patient_resolution_id if patient_resolution_id is not None else str(uuid4())
    )
    return reschedule_arguments(
        appointment_id=appointment_id
        if appointment_id is not None
        else original_appointment_id
        if original_appointment_id is not None
        else str(uuid4()),
        patient_resolution_id=resolved_patient_resolution_id,
        hold_id=resolved_hold_id,
        new_slot_id=resolved_slot_id,
        explicit_confirmation=explicit_confirmation,
        confirmation_text=confirmation_text,
        reschedule_reason=reschedule_reason,
    )


def reschedule_tool_request(
    *,
    appointment_id: str | None = None,
    original_appointment_id: str | None = None,
    patient_resolution_id: str | None = None,
    hold_id: str | None = None,
    new_slot_id: str | None = None,
    explicit_confirmation: bool = True,
    confirmation_text: str | None = "Yes, please reschedule it.",
    tool_call_id: str = TOOL_CALL_ID,
) -> RetellToolCallRequest:
    return RetellToolCallRequest.model_validate(
        {
            "provider_call_id": PROVIDER_CALL_ID,
            "tool_call_id": tool_call_id,
            "tool_name": "reschedule_appointment",
            "arguments": reschedule_arguments(
                appointment_id=appointment_id,
                original_appointment_id=original_appointment_id,
                patient_resolution_id=patient_resolution_id,
                hold_id=hold_id,
                new_slot_id=new_slot_id,
                explicit_confirmation=explicit_confirmation,
                confirmation_text=confirmation_text,
            ),
        },
    )


def create_retell_rescheduling_tool_context(
    *,
    original_status: AppointmentStatus = AppointmentStatus.SCHEDULED,
    new_slot_status: AvailabilitySlotStatus = AvailabilitySlotStatus.AVAILABLE,
    extra_patients: list[Any] | None = None,
) -> dict[str, Any]:
    clinic_time_service = make_test_clinic_time_service()
    rescheduling_context = create_rescheduling_context(
        original_status=original_status,
        new_slot_status=new_slot_status,
    )
    original_appointment = rescheduling_context.original_appointment
    hold = rescheduling_context.hold_service.create_hold(
        availability_slot_id=rescheduling_context.new_slot.id,
        doctor_id=rescheduling_context.doctor.id,
        start_time=rescheduling_context.new_slot.start_time,
        end_time=rescheduling_context.new_slot.end_time,
        owner_id=PROVIDER_CALL_ID,
    )

    patients = [rescheduling_context.patient, *(extra_patients or [])]
    patient_repository = LookupPatientRepo(patients)

    conversation_repository = cast(
        FakeConversationRepository,
        rescheduling_context.conversation_service.repository,
    )
    conversation = Conversation(
        id=uuid4(),
        channel=ConversationChannel.VOICE,
        status=ConversationStatus.ACTIVE,
        external_conversation_id=PROVIDER_CALL_ID,
        call_id=PROVIDER_CALL_ID,
        appointment_id=original_appointment.id,
        conversation_metadata={
            "voice_context": {
                "appointment_id": str(original_appointment.id),
                "hold_id": str(hold.hold_id),
                "availability_slot_id": str(rescheduling_context.new_slot.id),
                "start_time": rescheduling_context.new_slot.start_time.isoformat(),
                "end_time": rescheduling_context.new_slot.end_time.isoformat(),
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

    bridge = VoiceConversationBridgeService(
        voice_calls=voice_calls,
        conversations=conversation_repository,
        conversation_service=rescheduling_context.conversation_service,
    )

    tracking_rescheduling = TrackingAppointmentReschedulingService(
        rescheduling_context.service,
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
            patient_name=rescheduling_context.patient.full_name,
            patient_date_of_birth=rescheduling_context.patient.date_of_birth,
            provider_call_id=PROVIDER_CALL_ID,
            conversation_id=conversation.id,
            patient_email=rescheduling_context.patient.email,
        ),
    )
    patient_resolution_id = resolution_result.patient_resolution_id
    assert patient_resolution_id is not None

    scheduling_service = SchedulingService(
        specialties=FakeSpecialtyRepository([]),
        doctors=rescheduling_context.service.doctors,
        patients=FakePatientRepository(patients),
        availability_slots=rescheduling_context.slot_repository,
        appointments=rescheduling_context.appointment_repository,
    )

    voice_appointment_rescheduling = VoiceAppointmentReschedulingService(
        patient_identity_resolution=patient_identity_resolution,
        appointments=rescheduling_context.appointment_repository,
        appointment_rescheduling=cast(AppointmentReschedulingService, tracking_rescheduling),
        scheduling_metadata=scheduling_service,
        clinic_time_service=clinic_time_service,
    )
    voice_patient_appointment_lookup = VoicePatientAppointmentLookupService(
        patient_identity_resolution=patient_identity_resolution,
        appointments=rescheduling_context.appointment_repository,
        scheduling_metadata=scheduling_service,
        clinic_time_service=clinic_time_service,
    )

    tracking_voice_calls = TrackingVoiceCallRepository()
    tracking_voice_calls.voice_calls[PROVIDER_CALL_ID] = voice_call

    adapter = RetellToolCallingAdapter(
        scheduling_service=scheduling_service,
        hold_service=rescheduling_context.hold_service,
        voice_calls=tracking_voice_calls,
        voice_conversation_bridge=bridge,
        conversations=rescheduling_context.conversation_service,
        appointment_rescheduling=tracking_rescheduling,
        appointments=rescheduling_context.appointment_repository,
        clinic_time_service=clinic_time_service,
        patient_identity_resolution=patient_identity_resolution,
        voice_patient_appointment_lookup=voice_patient_appointment_lookup,
        voice_appointment_rescheduling=voice_appointment_rescheduling,
    )

    return {
        "adapter": adapter,
        "bridge": bridge,
        "tracking_rescheduling": tracking_rescheduling,
        "rescheduling_context": rescheduling_context,
        "conversation": conversation,
        "conversation_repository": conversation_repository,
        "original_appointment": original_appointment,
        "original_slot": rescheduling_context.original_slot,
        "patient": rescheduling_context.patient,
        "patient_resolution_id": patient_resolution_id,
        "hold": hold,
        "new_slot": rescheduling_context.new_slot,
        "voice_call": voice_call,
        "patient_identity_resolution": patient_identity_resolution,
        "appointment_repository": rescheduling_context.appointment_repository,
        "clinic_time_service": clinic_time_service,
    }
