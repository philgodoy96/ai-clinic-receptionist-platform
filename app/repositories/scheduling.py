from __future__ import annotations

from collections.abc import Sequence
from datetime import date, datetime
from typing import Protocol
from uuid import UUID

from app.models.scheduling import Appointment, AvailabilitySlot, Doctor, Patient, Specialty


class SpecialtyRepository(Protocol):
    def list_active(self) -> Sequence[Specialty]:
        raise NotImplementedError

    def get_by_name(self, name: str) -> Specialty | None:
        raise NotImplementedError

    def get_by_id(self, specialty_id: UUID) -> Specialty | None:
        raise NotImplementedError


class DoctorRepository(Protocol):
    def list_active(self, specialty_id: UUID | None = None) -> Sequence[Doctor]:
        raise NotImplementedError

    def get_by_id(self, doctor_id: UUID) -> Doctor | None:
        raise NotImplementedError


class PatientRepository(Protocol):
    def get_by_id(self, patient_id: UUID) -> Patient | None:
        raise NotImplementedError

    def get_by_email(self, email: str) -> Patient | None:
        raise NotImplementedError

    def get_by_phone_number(self, phone_number: str) -> Patient | None:
        raise NotImplementedError

    def get_by_identity(
        self,
        *,
        full_name: str,
        date_of_birth: date,
        phone_number: str | None = None,
        email: str | None = None,
    ) -> Patient | None:
        raise NotImplementedError

    def add(self, patient: Patient) -> Patient:
        raise NotImplementedError


class AvailabilitySlotRepository(Protocol):
    def get_by_id(self, slot_id: UUID) -> AvailabilitySlot | None:
        raise NotImplementedError

    def list_available(
        self,
        *,
        doctor_id: UUID,
        start_from: datetime,
        start_to: datetime,
    ) -> Sequence[AvailabilitySlot]:
        raise NotImplementedError


class AppointmentRepository(Protocol):
    def get_by_id(self, appointment_id: UUID) -> Appointment | None:
        raise NotImplementedError

    def list_upcoming_for_patient(
        self,
        *,
        patient_id: UUID,
        start_from: datetime,
    ) -> Sequence[Appointment]:
        raise NotImplementedError

    def find_scheduled_conflict(
        self,
        *,
        doctor_id: UUID,
        start_time: datetime,
    ) -> Appointment | None:
        raise NotImplementedError

    def add(self, appointment: Appointment) -> Appointment:
        raise NotImplementedError

    def find_by_rescheduled_from(
        self,
        *,
        appointment_id: UUID,
    ) -> Appointment | None:
        raise NotImplementedError