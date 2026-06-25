from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol
from uuid import UUID

from app.domain.appointment_rescheduling import (
    AppointmentReschedulingNotFoundError,
    AppointmentReschedulingNotReschedulableError,
    AppointmentReschedulingRequest,
)
from app.domain.audit.enums import AuditActorType
from app.domain.scheduling.enums import AppointmentStatus
from app.domain.voice_rescheduling import (
    _ALREADY_RESCHEDULED_MESSAGE,
    _HOLD_UNAVAILABLE_MESSAGE,
    _NEW_SLOT_UNAVAILABLE_MESSAGE,
    _NOT_RESCHEDULABLE_MESSAGE,
    VOICE_RESCHEDULING_SOURCE,
    AppointmentNotOwnedByPatientForReschedulingError,
    PatientResolutionRequiredForReschedulingError,
    VoiceAppointmentReschedulingRequest,
    VoiceAppointmentReschedulingResult,
    VoiceReschedulingMissingConfirmationError,
)
from app.models.scheduling import Appointment, Doctor, Specialty
from app.repositories.scheduling import AppointmentRepository
from app.services.appointment_rescheduling import AppointmentReschedulingService
from app.services.clinic_time import ClinicTimeService
from app.services.patient_identity_resolution import PatientIdentityResolutionService

_US_TIMEZONE_VOICE_LABELS = {
    "America/New_York": "Eastern",
    "America/Chicago": "Central",
    "America/Denver": "Mountain",
    "America/Los_Angeles": "Pacific",
}


class SchedulingMetadataForVoiceRescheduling(Protocol):
    def list_specialties(self) -> Sequence[Specialty]:
        raise NotImplementedError

    def list_doctors(self, specialty_id: UUID | None = None) -> Sequence[Doctor]:
        raise NotImplementedError


@dataclass(frozen=True, slots=True)
class _AppointmentPresentation:
    specialty_name: str
    doctor_name: str
    full_summary: str


class VoiceAppointmentReschedulingService:
    def __init__(
        self,
        *,
        patient_identity_resolution: PatientIdentityResolutionService,
        appointments: AppointmentRepository,
        appointment_rescheduling: AppointmentReschedulingService,
        scheduling_metadata: SchedulingMetadataForVoiceRescheduling,
        clinic_time_service: ClinicTimeService,
    ) -> None:
        self.patient_identity_resolution = patient_identity_resolution
        self.appointments = appointments
        self.appointment_rescheduling = appointment_rescheduling
        self.scheduling_metadata = scheduling_metadata
        self.clinic_time_service = clinic_time_service

    def reschedule_appointment(
        self,
        request: VoiceAppointmentReschedulingRequest,
    ) -> VoiceAppointmentReschedulingResult:
        self._validate_confirmation(request)

        resolution = self.patient_identity_resolution.get_resolution_for_booking(
            patient_resolution_id=request.patient_resolution_id,
            provider_call_id=request.provider_call_id,
            conversation_id=request.conversation_id,
        )
        if resolution is None:
            raise PatientResolutionRequiredForReschedulingError

        original = self.appointments.get_by_id(request.appointment_id)
        if original is None:
            raise AppointmentReschedulingNotFoundError()

        if original.patient_id != resolution.patient_id:
            raise AppointmentNotOwnedByPatientForReschedulingError

        original_presentation = self._present_appointment(original)

        if original.status == AppointmentStatus.RESCHEDULED:
            reschedule_result = self.appointment_rescheduling.reschedule_appointment(
                self._build_rescheduling_request(request, original),
            )
            new_appointment = self.appointments.get_by_id(reschedule_result.new_appointment_id)
            new_presentation = (
                self._present_appointment(new_appointment)
                if new_appointment is not None
                else original_presentation
            )
            return VoiceAppointmentReschedulingResult(
                original_appointment_id=reschedule_result.original_appointment_id,
                new_appointment_id=reschedule_result.new_appointment_id,
                status=AppointmentStatus.SCHEDULED.value,
                human_readable_summary=new_presentation.full_summary,
                suggested_response_text=_ALREADY_RESCHEDULED_MESSAGE,
                already_rescheduled=True,
                duplicate=reschedule_result.duplicate,
                confirmation_email_created=reschedule_result.confirmation_email_created,
            )

        self._validate_voice_rescheduling_eligibility(original)

        reschedule_result = self.appointment_rescheduling.reschedule_appointment(
            self._build_rescheduling_request(request, original),
        )

        new_appointment = self.appointments.get_by_id(reschedule_result.new_appointment_id)
        new_presentation = (
            self._present_appointment(new_appointment)
            if new_appointment is not None
            else original_presentation
        )

        return VoiceAppointmentReschedulingResult(
            original_appointment_id=reschedule_result.original_appointment_id,
            new_appointment_id=reschedule_result.new_appointment_id,
            status=AppointmentStatus.SCHEDULED.value,
            human_readable_summary=new_presentation.full_summary,
            suggested_response_text=(
                f"Your appointment has been rescheduled to {new_presentation.full_summary}."
            ),
            already_rescheduled=reschedule_result.already_rescheduled,
            duplicate=reschedule_result.duplicate,
            confirmation_email_created=reschedule_result.confirmation_email_created,
        )

    def _validate_confirmation(self, request: VoiceAppointmentReschedulingRequest) -> None:
        if not request.explicit_confirmation:
            msg = "explicit_confirmation is required"
            raise VoiceReschedulingMissingConfirmationError(msg)

        if request.confirmation_text is None or not request.confirmation_text.strip():
            msg = "confirmation_text is required"
            raise VoiceReschedulingMissingConfirmationError(msg)

    def _validate_voice_rescheduling_eligibility(self, appointment: Appointment) -> None:
        if appointment.status != AppointmentStatus.SCHEDULED:
            msg = "appointment cannot be rescheduled"
            raise AppointmentReschedulingNotReschedulableError(msg)

        clinic_now = self.clinic_time_service.clinic_now()
        if appointment.start_time < clinic_now:
            msg = "past appointments cannot be rescheduled"
            raise AppointmentReschedulingNotReschedulableError(msg)

    def _build_rescheduling_request(
        self,
        request: VoiceAppointmentReschedulingRequest,
        appointment: Appointment,
    ) -> AppointmentReschedulingRequest:
        return AppointmentReschedulingRequest(
            appointment_id=appointment.id,
            hold_id=request.hold_id,
            new_slot_id=request.new_slot_id,
            explicit_confirmation=request.explicit_confirmation,
            idempotency_key=request.idempotency_key,
            owner_id=request.provider_call_id,
            rescheduling_reason=request.rescheduling_reason,
            source=VOICE_RESCHEDULING_SOURCE,
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


def voice_rescheduling_hold_unavailable_message() -> str:
    return _HOLD_UNAVAILABLE_MESSAGE


def voice_rescheduling_new_slot_unavailable_message() -> str:
    return _NEW_SLOT_UNAVAILABLE_MESSAGE


def voice_rescheduling_not_reschedulable_message() -> str:
    return _NOT_RESCHEDULABLE_MESSAGE
