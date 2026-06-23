from __future__ import annotations

from datetime import UTC, date, datetime, time, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.session import SessionLocal
from app.domain.scheduling.enums import AvailabilitySlotStatus
from app.models.scheduling import AvailabilitySlot, Doctor, Patient, Specialty


def main() -> None:
    with SessionLocal() as session:
        seed_specialties(session)
        session.flush()

        seed_doctors(session)
        session.flush()

        seed_patients(session)
        session.flush()

        seed_availability_slots(session)
        session.commit()

    print("Demo scheduling data seeded successfully.")


def seed_specialties(session: Session) -> None:
    specialties = [
        (
            "Dermatology",
            "Skin, hair, and nail care for common dermatology concerns.",
        ),
        (
            "Cardiology",
            "Heart health consultations and cardiovascular follow-up visits.",
        ),
        (
            "Primary Care",
            "General health visits, preventive care, and routine follow-ups.",
        ),
    ]

    for name, description in specialties:
        existing = session.scalar(select(Specialty).where(Specialty.name == name))

        if existing is None:
            session.add(Specialty(name=name, description=description))


def seed_doctors(session: Session) -> None:
    doctors = [
        {
            "full_name": "Dr. Emily Carter",
            "email": "emily.carter@example-clinic.test",
            "phone_number": "+1-555-0101",
            "specialty": "Dermatology",
        },
        {
            "full_name": "Dr. Michael Reed",
            "email": "michael.reed@example-clinic.test",
            "phone_number": "+1-555-0102",
            "specialty": "Cardiology",
        },
        {
            "full_name": "Dr. Sarah Mitchell",
            "email": "sarah.mitchell@example-clinic.test",
            "phone_number": "+1-555-0103",
            "specialty": "Primary Care",
        },
    ]

    for doctor_data in doctors:
        existing = session.scalar(
            select(Doctor).where(Doctor.email == doctor_data["email"]),
        )

        if existing is not None:
            continue

        specialty = session.scalar(
            select(Specialty).where(Specialty.name == doctor_data["specialty"]),
        )

        if specialty is None:
            raise RuntimeError(f"Missing specialty: {doctor_data['specialty']}")

        session.add(
            Doctor(
                full_name=doctor_data["full_name"],
                email=doctor_data["email"],
                phone_number=doctor_data["phone_number"],
                specialty_id=specialty.id,
            ),
        )


def seed_patients(session: Session) -> None:
    patients = [
        {
            "full_name": "John Miller",
            "date_of_birth": date(1985, 4, 12),
            "phone_number": "+1-555-0201",
            "email": "john.miller@example.test",
        },
        {
            "full_name": "Ava Thompson",
            "date_of_birth": date(1992, 9, 3),
            "phone_number": "+1-555-0202",
            "email": "ava.thompson@example.test",
        },
    ]

    for patient_data in patients:
        existing = session.scalar(
            select(Patient).where(Patient.email == patient_data["email"]),
        )

        if existing is None:
            session.add(Patient(**patient_data))


def seed_availability_slots(session: Session) -> None:
    doctors = session.scalars(select(Doctor)).all()
    slot_dates = next_weekdays(count=5)

    for doctor in doctors:
        for slot_date in slot_dates:
            for slot_start in [time(9, 0), time(10, 0), time(14, 0), time(15, 0)]:
                start_time = datetime.combine(slot_date, slot_start, tzinfo=UTC)
                end_time = start_time + timedelta(minutes=30)

                existing = session.scalar(
                    select(AvailabilitySlot).where(
                        AvailabilitySlot.doctor_id == doctor.id,
                        AvailabilitySlot.start_time == start_time,
                    ),
                )

                if existing is None:
                    session.add(
                        AvailabilitySlot(
                            doctor_id=doctor.id,
                            start_time=start_time,
                            end_time=end_time,
                            status=AvailabilitySlotStatus.AVAILABLE,
                        ),
                    )


def next_weekdays(count: int) -> list[date]:
    days: list[date] = []
    current = datetime.now(UTC).date() + timedelta(days=1)

    while len(days) < count:
        if current.weekday() < 5:
            days.append(current)

        current += timedelta(days=1)

    return days


if __name__ == "__main__":
    main()
