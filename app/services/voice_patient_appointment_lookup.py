from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol
from uuid import UUID

from app.domain.voice_appointment_lookup import (
    ListPatientAppointmentsRequest,
    ListPatientAppointmentsResult,
    PatientAppointmentLookupNextStep,
    PatientResolutionRequiredForAppointmentLookupError,
    VoiceAppointmentSummary,
)
from app.models.scheduling import Appointment, Doctor, Specialty
from app.repositories.scheduling import AppointmentRepository
from app.services.clinic_time import ClinicTimeService
from app.services.patient_identity_resolution import PatientIdentityResolutionService

DEFAULT_LIST_PATIENT_APPOINTMENTS_LIMIT = 5

_PATIENT_RESOLUTION_RETRY_MESSAGE = (
    "I need to verify your profile again before I can look up appointments."
)
_NO_UPCOMING_APPOINTMENTS_MESSAGE = (
    "I am not seeing any upcoming appointments for that profile. "
    "Would you like help scheduling a new appointment instead?"
)

_US_TIMEZONE_VOICE_LABELS = {
    "America/New_York": "Eastern",
    "America/Chicago": "Central",
    "America/Denver": "Mountain",
    "America/Los_Angeles": "Pacific",
}


class SchedulingMetadataForAppointmentLookup(Protocol):
    def list_specialties(self) -> Sequence[Specialty]:
        raise NotImplementedError

    def list_doctors(self, specialty_id: UUID | None = None) -> Sequence[Doctor]:
        raise NotImplementedError


@dataclass(frozen=True, slots=True)
class _AppointmentPresentation:
    specialty_name: str
    doctor_name: str
    full_summary: str
    concise_summary: str


class VoicePatientAppointmentLookupService:
    def __init__(
        self,
        *,
        patient_identity_resolution: PatientIdentityResolutionService,
        appointments: AppointmentRepository,
        scheduling_metadata: SchedulingMetadataForAppointmentLookup,
        clinic_time_service: ClinicTimeService,
    ) -> None:
        self.patient_identity_resolution = patient_identity_resolution
        self.appointments = appointments
        self.scheduling_metadata = scheduling_metadata
        self.clinic_time_service = clinic_time_service

    def list_patient_appointments(
        self,
        request: ListPatientAppointmentsRequest,
    ) -> ListPatientAppointmentsResult:
        resolution = self.patient_identity_resolution.get_resolution_for_booking(
            patient_resolution_id=request.patient_resolution_id,
            provider_call_id=request.provider_call_id,
            conversation_id=request.conversation_id,
        )
        if resolution is None:
            raise PatientResolutionRequiredForAppointmentLookupError

        start_from = self.clinic_time_service.clinic_now()
        upcoming = self.appointments.list_upcoming_for_patient(
            patient_id=resolution.patient_id,
            start_from=start_from,
        )
        ordered = sorted(upcoming, key=lambda appointment: appointment.start_time)
        limited = list(ordered[: request.limit])

        if not limited:
            return ListPatientAppointmentsResult(
                appointment_count=0,
                appointments=[],
                next_step=PatientAppointmentLookupNextStep.NO_UPCOMING_APPOINTMENTS,
                suggested_response_text=_NO_UPCOMING_APPOINTMENTS_MESSAGE,
            )

        summaries = [
            self._build_summary(appointment) for appointment in limited
        ]

        if len(summaries) == 1:
            summary = summaries[0]
            return ListPatientAppointmentsResult(
                appointment_count=1,
                appointments=summaries,
                next_step=PatientAppointmentLookupNextStep.CONFIRM_APPOINTMENT_SELECTION,
                suggested_response_text=(
                    f"I found your {summary.human_readable_summary}. "
                    "Is that the appointment you want to change?"
                ),
            )

        options_text = _join_appointment_options(
            [presentation.concise_summary for presentation in self._present_appointments(limited)],
        )
        return ListPatientAppointmentsResult(
            appointment_count=len(summaries),
            appointments=summaries,
            next_step=PatientAppointmentLookupNextStep.CHOOSE_APPOINTMENT,
            suggested_response_text=(
                f"I found {len(summaries)} upcoming appointments: {options_text}. "
                "Which one would you like to change?"
            ),
        )

    def _build_summary(self, appointment: Appointment) -> VoiceAppointmentSummary:
        presentation = self._present_appointment(appointment)
        localized_start = appointment.start_time.astimezone(self.clinic_time_service.timezone)
        localized_end = appointment.end_time.astimezone(self.clinic_time_service.timezone)

        return VoiceAppointmentSummary(
            appointment_id=str(appointment.id),
            start_time=localized_start.isoformat(),
            end_time=localized_end.isoformat(),
            doctor_name=presentation.doctor_name,
            specialty_name=presentation.specialty_name,
            status=appointment.status.value,
            human_readable_summary=presentation.full_summary,
        )

    def _present_appointments(
        self,
        appointments: list[Appointment],
    ) -> list[_AppointmentPresentation]:
        return [self._present_appointment(appointment) for appointment in appointments]

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
            concise_summary=_format_concise_appointment_summary(
                specialty_name=specialty_name,
                doctor_name=doctor_name,
                start_time=localized_start,
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

        return "your appointment"


def build_patient_resolution_retry_response() -> dict[str, str]:
    return {
        "suggested_response_text": _PATIENT_RESOLUTION_RETRY_MESSAGE,
    }


def _join_appointment_options(summaries: list[str]) -> str:
    if not summaries:
        return ""

    if len(summaries) == 1:
        return summaries[0]

    if len(summaries) == 2:
        return f"{summaries[0]}, and {summaries[1]}"

    return ", ".join(summaries[:-1]) + f", and {summaries[-1]}"


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
        f"{specialty_name} with {doctor_name} on {date_part} at {time_part} {timezone_label}"
    )


def _format_concise_appointment_summary(
    *,
    specialty_name: str,
    doctor_name: str,
    start_time: datetime,
) -> str:
    weekday = start_time.strftime("%A")
    time_part = start_time.strftime("%I:%M %p").lstrip("0")

    return f"{specialty_name} with {doctor_name} on {weekday} at {time_part}"


def _voice_timezone_label(timezone_name: str, start_time: datetime) -> str:
    mapped = _US_TIMEZONE_VOICE_LABELS.get(timezone_name.strip())
    if mapped is not None:
        return mapped

    tzname = start_time.tzname()
    if tzname:
        return tzname

    return timezone_name
