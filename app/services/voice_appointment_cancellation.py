from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol
from uuid import UUID

from app.domain.appointments import (
    AppointmentCancellationRequest,
    AppointmentNotCancelableError,
    AppointmentNotFoundError,
)
from app.domain.audit.enums import AuditActorType
from app.domain.scheduling.enums import AppointmentStatus
from app.domain.voice_cancellation import (
    _ALREADY_CANCELLED_MESSAGE,
    VOICE_CANCELLATION_SOURCE,
    AppointmentNotOwnedByPatientError,
    PatientResolutionRequiredForCancellationError,
    VoiceAppointmentCancellationRequest,
    VoiceAppointmentCancellationResult,
    VoiceCancellationMissingConfirmationError,
)
from app.models.scheduling import Appointment, Doctor, Specialty
from app.repositories.scheduling import AppointmentRepository
from app.services.appointment_cancellation import AppointmentCancellationService
from app.services.clinic_time import ClinicTimeService
from app.services.patient_identity_resolution import PatientIdentityResolutionService

_US_TIMEZONE_VOICE_LABELS = {
    "America/New_York": "Eastern",
    "America/Chicago": "Central",
    "America/Denver": "Mountain",
    "America/Los_Angeles": "Pacific",
}


class SchedulingMetadataForVoiceCancellation(Protocol):
    def list_specialties(self) -> Sequence[Specialty]:
        raise NotImplementedError

    def list_doctors(self, specialty_id: UUID | None = None) -> Sequence[Doctor]:
        raise NotImplementedError


@dataclass(frozen=True, slots=True)
class _AppointmentPresentation:
    specialty_name: str
    doctor_name: str
    full_summary: str


class VoiceAppointmentCancellationService:
    def __init__(
        self,
        *,
        patient_identity_resolution: PatientIdentityResolutionService,
        appointments: AppointmentRepository,
        appointment_cancellation: AppointmentCancellationService,
        scheduling_metadata: SchedulingMetadataForVoiceCancellation,
        clinic_time_service: ClinicTimeService,
    ) -> None:
        self.patient_identity_resolution = patient_identity_resolution
        self.appointments = appointments
        self.appointment_cancellation = appointment_cancellation
        self.scheduling_metadata = scheduling_metadata
        self.clinic_time_service = clinic_time_service

    def cancel_appointment(
        self,
        request: VoiceAppointmentCancellationRequest,
    ) -> VoiceAppointmentCancellationResult:
        self._validate_confirmation(request)

        resolution = self.patient_identity_resolution.get_resolution_for_booking(
            patient_resolution_id=request.patient_resolution_id,
            provider_call_id=request.provider_call_id,
            conversation_id=request.conversation_id,
        )
        if resolution is None:
            raise PatientResolutionRequiredForCancellationError

        appointment = self.appointments.get_by_id(request.appointment_id)
        if appointment is None:
            msg = "appointment was not found"
            raise AppointmentNotFoundError(msg)

        if appointment.patient_id != resolution.patient_id:
            raise AppointmentNotOwnedByPatientError

        presentation = self._present_appointment(appointment)

        if appointment.status == AppointmentStatus.CANCELLED:
            cancellation_result = self.appointment_cancellation.cancel_appointment(
                self._build_cancellation_request(request, appointment),
            )
            return VoiceAppointmentCancellationResult(
                appointment_id=appointment.id,
                status=AppointmentStatus.CANCELLED.value,
                cancelled_at=appointment.cancelled_at,
                human_readable_summary=presentation.full_summary,
                suggested_response_text=_ALREADY_CANCELLED_MESSAGE,
                already_cancelled=True,
                duplicate=cancellation_result.duplicate,
            )

        self._validate_voice_cancellation_eligibility(appointment)

        cancellation_result = self.appointment_cancellation.cancel_appointment(
            self._build_cancellation_request(request, appointment),
        )

        cancelled_appointment = self.appointments.get_by_id(appointment.id)
        cancelled_at = (
            cancelled_appointment.cancelled_at
            if cancelled_appointment is not None
            else None
        )

        return VoiceAppointmentCancellationResult(
            appointment_id=cancellation_result.appointment_id,
            status=AppointmentStatus.CANCELLED.value,
            cancelled_at=cancelled_at,
            human_readable_summary=presentation.full_summary,
            suggested_response_text=(
                f"Your {presentation.full_summary} has been cancelled."
            ),
            already_cancelled=False,
            duplicate=cancellation_result.duplicate,
        )

    def _validate_confirmation(self, request: VoiceAppointmentCancellationRequest) -> None:
        if not request.explicit_confirmation:
            msg = "explicit_confirmation is required"
            raise VoiceCancellationMissingConfirmationError(msg)

        if request.confirmation_text is None or not request.confirmation_text.strip():
            msg = "confirmation_text is required"
            raise VoiceCancellationMissingConfirmationError(msg)

    def _validate_voice_cancellation_eligibility(self, appointment: Appointment) -> None:
        if appointment.status != AppointmentStatus.SCHEDULED:
            msg = "appointment cannot be cancelled"
            raise AppointmentNotCancelableError(msg)

        clinic_now = self.clinic_time_service.clinic_now()
        if appointment.start_time < clinic_now:
            msg = "past appointments cannot be cancelled"
            raise AppointmentNotCancelableError(msg)

    def _build_cancellation_request(
        self,
        request: VoiceAppointmentCancellationRequest,
        appointment: Appointment,
    ) -> AppointmentCancellationRequest:
        return AppointmentCancellationRequest(
            appointment_id=appointment.id,
            explicit_confirmation=request.explicit_confirmation,
            idempotency_key=request.idempotency_key,
            cancellation_reason=request.cancellation_reason,
            source=VOICE_CANCELLATION_SOURCE,
            actor_type=AuditActorType.RETELL,
            actor_id=request.provider_call_id,
            call_id=request.call_id or request.provider_call_id,
            conversation_id=(
                str(request.conversation_id) if request.conversation_id is not None else None
            ),
        )

    def _present_appointment(self, appointment: Appointment) -> _AppointmentPresentation:
        doctor_name = self._resolve_doctor_name(appointment.doctor_id)
        specialty_name = self._resolve_specialty_name(appointment.specialty_id)
        localized_start = appointment.start_time.astimezone(self.clinic_time_service.timezone)

        return _AppointmentPresentation(
            specialty_name=specialty_name,
            doctor_name=doctor_name,
            full_summary=_format_full_appointment_summary(
                specialty_name=specialty_name,
                doctor_name=doctor_name,
                start_time=localized_start,
                timezone_name=self.clinic_time_service.get_current_clinic_context().clinic_timezone,
            ),
        )

    def _resolve_doctor_name(self, doctor_id: UUID) -> str:
        for doctor in self.scheduling_metadata.list_doctors():
            if doctor.id == doctor_id:
                return doctor.full_name

        return "your doctor"

    def _resolve_specialty_name(self, specialty_id: UUID) -> str:
        for specialty in self.scheduling_metadata.list_specialties():
            if specialty.id == specialty_id:
                return specialty.name

        return "appointment"


def _format_full_appointment_summary(
    *,
    specialty_name: str,
    doctor_name: str,
    start_time: datetime,
    timezone_name: str,
) -> str:
    date_part = f"{start_time.strftime('%A')}, {start_time.strftime('%B')} {start_time.day}"
    time_part = start_time.strftime("%I:%M %p").lstrip("0")
    timezone_label = _voice_timezone_label(timezone_name, start_time)

    return (
        f"{specialty_name} appointment with {doctor_name} on {date_part} at "
        f"{time_part} {timezone_label}"
    )


def _voice_timezone_label(timezone_name: str, start_time: datetime) -> str:
    mapped = _US_TIMEZONE_VOICE_LABELS.get(timezone_name.strip())
    if mapped is not None:
        return mapped

    tzname = start_time.tzname()
    if tzname:
        return tzname

    return timezone_name
