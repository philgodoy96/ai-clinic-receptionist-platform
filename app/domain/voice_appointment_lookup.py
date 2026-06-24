from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Any
from uuid import UUID


class PatientAppointmentLookupNextStep(StrEnum):
    CONFIRM_APPOINTMENT_SELECTION = "confirm_appointment_selection"
    CHOOSE_APPOINTMENT = "choose_appointment"
    NO_UPCOMING_APPOINTMENTS = "no_upcoming_appointments"


@dataclass(frozen=True, slots=True)
class ListPatientAppointmentsRequest:
    patient_resolution_id: str
    provider_call_id: str
    conversation_id: UUID | None = None
    limit: int = 5


@dataclass(frozen=True, slots=True)
class VoiceAppointmentSummary:
    appointment_id: str
    start_time: str
    end_time: str
    doctor_name: str
    specialty_name: str
    status: str
    human_readable_summary: str

    def to_tool_result(self) -> dict[str, str]:
        return {
            "appointment_id": self.appointment_id,
            "start_time": self.start_time,
            "end_time": self.end_time,
            "doctor_name": self.doctor_name,
            "specialty_name": self.specialty_name,
            "status": self.status,
            "human_readable_summary": self.human_readable_summary,
        }


@dataclass(frozen=True, slots=True)
class ListPatientAppointmentsResult:
    appointment_count: int
    appointments: list[VoiceAppointmentSummary]
    next_step: PatientAppointmentLookupNextStep
    suggested_response_text: str

    def to_tool_result(self) -> dict[str, Any]:
        return {
            "appointment_count": self.appointment_count,
            "appointments": [
                appointment.to_tool_result() for appointment in self.appointments
            ],
            "next_step": self.next_step.value,
            "suggested_response_text": self.suggested_response_text,
        }


class PatientResolutionRequiredForAppointmentLookupError(Exception):
    """Raised when appointment lookup lacks a valid scoped patient resolution token."""
