from app.domain.scheduling.enums import AppointmentStatus, AvailabilitySlotStatus
from app.models.scheduling import Appointment, AvailabilitySlot, Doctor, Patient, Specialty


def test_availability_slot_status_values() -> None:
    assert AvailabilitySlotStatus.AVAILABLE.value == "available"
    assert AvailabilitySlotStatus.HELD.value == "held"
    assert AvailabilitySlotStatus.BOOKED.value == "booked"
    assert AvailabilitySlotStatus.BLOCKED.value == "blocked"


def test_appointment_status_values() -> None:
    assert AppointmentStatus.SCHEDULED.value == "scheduled"
    assert AppointmentStatus.RESCHEDULED.value == "rescheduled"
    assert AppointmentStatus.CANCELLED.value == "cancelled"
    assert AppointmentStatus.COMPLETED.value == "completed"


def test_scheduling_model_table_names() -> None:
    assert Specialty.__tablename__ == "specialties"
    assert Doctor.__tablename__ == "doctors"
    assert Patient.__tablename__ == "patients"
    assert AvailabilitySlot.__tablename__ == "availability_slots"
    assert Appointment.__tablename__ == "appointments"


def test_patient_model_uses_safe_identity_columns() -> None:
    columns = get_model_columns(Patient)

    assert {"full_name", "date_of_birth", "phone_number", "email"}.issubset(columns)
    assert "ssn" not in columns
    assert "cpf" not in columns
    assert "insurance_member_id" not in columns


def test_appointment_model_has_scheduling_columns() -> None:
    columns = get_model_columns(Appointment)

    assert {
        "patient_id",
        "doctor_id",
        "specialty_id",
        "availability_slot_id",
        "start_time",
        "end_time",
        "status",
    }.issubset(columns)


def get_model_columns(model: type[object]) -> set[str]:
    return set(model.__table__.columns.keys())  # type: ignore[attr-defined]
