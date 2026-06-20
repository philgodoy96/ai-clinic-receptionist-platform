from __future__ import annotations

from collections.abc import Sequence
from datetime import date, datetime
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.domain.scheduling.enums import AppointmentStatus, AvailabilitySlotStatus
from app.domain.scheduling.phone import normalize_phone_digits
from app.models.scheduling import Appointment, AvailabilitySlot, Doctor, Patient, Specialty


class SQLAlchemySpecialtyRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def list_active(self) -> Sequence[Specialty]:
        statement = (
            select(Specialty)
            .where(Specialty.is_active.is_(True))
            .order_by(Specialty.name)
        )

        return list(self.session.scalars(statement).all())

    def get_by_name(self, name: str) -> Specialty | None:
        statement = select(Specialty).where(Specialty.name == name)

        return self.session.scalar(statement)

    def get_by_id(self, specialty_id: UUID) -> Specialty | None:
        return self.session.get(Specialty, specialty_id)


class SQLAlchemyDoctorRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def list_active(self, specialty_id: UUID | None = None) -> Sequence[Doctor]:
        statement = select(Doctor).where(Doctor.is_active.is_(True))

        if specialty_id is not None:
            statement = statement.where(Doctor.specialty_id == specialty_id)

        statement = statement.order_by(Doctor.full_name)

        return list(self.session.scalars(statement).all())

    def get_by_id(self, doctor_id: UUID) -> Doctor | None:
        return self.session.get(Doctor, doctor_id)


class SQLAlchemyPatientRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def get_by_id(self, patient_id: UUID) -> Patient | None:
        return self.session.get(Patient, patient_id)

    def get_by_email(self, email: str) -> Patient | None:
        statement = select(Patient).where(Patient.email == email)

        return self.session.scalar(statement)

    def get_by_phone_number(self, phone_number: str) -> Patient | None:
        statement = select(Patient).where(Patient.phone_number == phone_number)

        return self.session.scalar(statement)

    def get_by_identity(
        self,
        *,
        full_name: str,
        date_of_birth: date,
        phone_number: str | None = None,
        email: str | None = None,
    ) -> Patient | None:
        if phone_number is None and email is None:
            return None

        statement = select(Patient).where(
            Patient.full_name == full_name,
            Patient.date_of_birth == date_of_birth,
        )

        if email is not None:
            statement = statement.where(Patient.email == email)

        candidates = list(self.session.scalars(statement).all())

        if not candidates:
            return None

        if phone_number is None:
            return candidates[0]

        normalized_phone = normalize_phone_digits(phone_number)
        for patient in candidates:
            if normalize_phone_digits(patient.phone_number) == normalized_phone:
                return patient

        return None

    def add(self, patient: Patient) -> Patient:
        self.session.add(patient)
        self.session.flush()

        return patient


class SQLAlchemyAvailabilitySlotRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def get_by_id(self, slot_id: UUID) -> AvailabilitySlot | None:
        return self.session.get(AvailabilitySlot, slot_id)

    def list_available(
        self,
        *,
        doctor_id: UUID,
        start_from: datetime,
        start_to: datetime,
    ) -> Sequence[AvailabilitySlot]:
        statement = (
            select(AvailabilitySlot)
            .where(
                AvailabilitySlot.doctor_id == doctor_id,
                AvailabilitySlot.status == AvailabilitySlotStatus.AVAILABLE,
                AvailabilitySlot.start_time >= start_from,
                AvailabilitySlot.start_time < start_to,
            )
            .order_by(AvailabilitySlot.start_time)
        )

        return list(self.session.scalars(statement).all())


class SQLAlchemyAppointmentRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def get_by_id(self, appointment_id: UUID) -> Appointment | None:
        return self.session.get(Appointment, appointment_id)

    def list_upcoming_for_patient(
        self,
        *,
        patient_id: UUID,
        start_from: datetime,
    ) -> Sequence[Appointment]:
        statement = (
            select(Appointment)
            .where(
                Appointment.patient_id == patient_id,
                Appointment.status == AppointmentStatus.SCHEDULED,
                Appointment.start_time >= start_from,
            )
            .order_by(Appointment.start_time)
        )

        return list(self.session.scalars(statement).all())

    def find_scheduled_conflict(
        self,
        *,
        doctor_id: UUID,
        start_time: datetime,
    ) -> Appointment | None:
        statement = select(Appointment).where(
            Appointment.doctor_id == doctor_id,
            Appointment.start_time == start_time,
            Appointment.status == AppointmentStatus.SCHEDULED,
        )

        return self.session.scalar(statement)

    def add(self, appointment: Appointment) -> Appointment:
        self.session.add(appointment)
        self.session.flush()

        return appointment