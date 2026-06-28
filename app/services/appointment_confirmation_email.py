from __future__ import annotations

from typing import Any
from uuid import UUID

from app.models.scheduling import Appointment, Doctor, Patient
from app.repositories.scheduling import SpecialtyRepository
from app.services.email_jobs import AppointmentConfirmationEmailJobCreate


def optional_non_empty_str(value: str | None) -> str | None:
    if value is None:
        return None

    stripped = value.strip()
    return stripped or None


def resolve_specialty_name(
    *,
    specialty_id: UUID,
    specialties: SpecialtyRepository | None,
) -> str | None:
    if specialties is None:
        return None

    specialty = specialties.get_by_id(specialty_id)
    if specialty is None:
        return None

    return optional_non_empty_str(specialty.name)


def build_appointment_confirmation_email_job_create(
    *,
    appointment: Appointment,
    source: str,
    patient: Patient | None = None,
    doctor: Doctor | None = None,
    specialty_name: str | None = None,
    extra_payload: dict[str, Any] | None = None,
) -> AppointmentConfirmationEmailJobCreate:
    recipient_email = optional_non_empty_str(patient.email) if patient else None
    patient_name = optional_non_empty_str(patient.full_name) if patient else None
    doctor_name = optional_non_empty_str(doctor.full_name) if doctor else None

    job_payload: dict[str, Any] = {"source": source}
    if specialty_name is not None:
        job_payload["specialty_name"] = specialty_name
    if extra_payload:
        job_payload.update(extra_payload)

    return AppointmentConfirmationEmailJobCreate(
        appointment_id=appointment.id,
        patient_id=appointment.patient_id,
        recipient_email=recipient_email,
        patient_name=patient_name,
        doctor_name=doctor_name,
        appointment_start_time=appointment.start_time.isoformat(),
        payload=job_payload,
    )
