from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any
from uuid import UUID

from app.domain.jobs.enums import EmailJobStatus, EmailJobType
from app.models.email_jobs import EmailJob
from app.repositories.email_jobs import EmailJobRepository


@dataclass(frozen=True, slots=True)
class AppointmentConfirmationEmailJobCreate:
    appointment_id: UUID
    patient_id: UUID
    recipient_email: str | None = None
    patient_name: str | None = None
    doctor_name: str | None = None
    appointment_start_time: str | None = None
    payload: dict[str, Any] = field(default_factory=dict)


class EmailJobService:
    def __init__(self, *, repository: EmailJobRepository) -> None:
        self.repository = repository

    def enqueue_appointment_confirmation(
        self,
        payload: AppointmentConfirmationEmailJobCreate,
    ) -> EmailJob:
        subject = "Appointment confirmation"
        body = self._build_confirmation_body(payload)
        email_job = EmailJob(
            job_type=EmailJobType.APPOINTMENT_CONFIRMATION,
            status=EmailJobStatus.PENDING,
            appointment_id=payload.appointment_id,
            patient_id=payload.patient_id,
            recipient_email=payload.recipient_email,
            subject=subject,
            body=body,
            attempts=0,
            max_attempts=3,
            payload={
                "patient_name": payload.patient_name,
                "doctor_name": payload.doctor_name,
                "appointment_start_time": payload.appointment_start_time,
                **payload.payload,
            },
        )

        return self.repository.add(email_job)

    def _build_confirmation_body(
        self,
        payload: AppointmentConfirmationEmailJobCreate,
    ) -> str:
        patient_name = payload.patient_name or "there"
        appointment_time = payload.appointment_start_time or "the scheduled time"
        doctor_name = payload.doctor_name or "your clinician"

        return (
            f"Hello {patient_name}, your appointment with {doctor_name} "
            f"has been confirmed for {appointment_time}."
        )