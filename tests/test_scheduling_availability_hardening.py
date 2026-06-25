from __future__ import annotations

import logging
from collections.abc import Sequence
from datetime import date, datetime, timedelta
from typing import Any, cast
from uuid import UUID, uuid4

import pytest
from redis.exceptions import RedisError

from app.domain.appointments import AppointmentCancellationRequest
from app.domain.scheduling.appointment_holds import AppointmentHold
from app.domain.scheduling.enums import AppointmentStatus, AvailabilitySlotStatus
from app.models.scheduling import Appointment, AvailabilitySlot, Doctor, Patient
from app.services.appointment_booking import (
    AppointmentBookingRequest,
    AppointmentBookingService,
)
from app.services.appointment_cancellation import AppointmentCancellationService
from app.services.appointment_holds import (
    AppointmentHoldNotFoundError,
    AppointmentHoldService,
    AppointmentHoldStoreUnavailableError,
)
from app.services.scheduling import SchedulingAvailabilityPolicy, SchedulingService
from tests.clinic_time_test_support import (
    REFERENCE_CLINIC_NOW_UTC,
    make_test_clinic_time_service,
)
from tests.test_appointment_booking_api import FakeAuditLogService
from tests.test_appointment_booking_service import (
    FakeAppointmentRepository,
    FakeAvailabilitySlotRepository,
    FakeDoctorRepository,
    FakePatientRepository,
)
from tests.test_appointment_cancellation_service import (
    FakeAppointmentCancellationAttemptRepository,
)
from tests.test_appointment_holds import FakeAppointmentHoldRepository
from tests.test_scheduling_services import (
    FakeDoctorRepository as SchedulingFakeDoctorRepository,
)
from tests.test_scheduling_services import (
    FakeSpecialtyRepository,
    create_availability_slot,
    create_doctor,
    create_specialty,
)


def _default_policy() -> SchedulingAvailabilityPolicy:
    return SchedulingAvailabilityPolicy(
        min_booking_lead_minutes=60,
        booking_horizon_days=14,
    )


def _earliest_bookable_utc() -> datetime:
    return REFERENCE_CLINIC_NOW_UTC + timedelta(minutes=60)


def _latest_bookable_utc() -> datetime:
    return REFERENCE_CLINIC_NOW_UTC + timedelta(days=14)


def _build_scheduling_service(
    *,
    doctor: Doctor,
    availability_slots: Sequence[AvailabilitySlot],
    hold_service: AppointmentHoldService | None = None,
    availability_policy: SchedulingAvailabilityPolicy | None = None,
) -> SchedulingService:
    return SchedulingService(
        specialties=FakeSpecialtyRepository([]),
        doctors=SchedulingFakeDoctorRepository([doctor]),
        patients=FakePatientRepository([]),
        availability_slots=FakeAvailabilitySlotRepository(list(availability_slots)),
        appointments=FakeAppointmentRepository([]),
        clinic_time_service=make_test_clinic_time_service(),
        hold_service=hold_service,
        availability_policy=availability_policy or _default_policy(),
    )


def _query_window() -> tuple[datetime, datetime]:
    return REFERENCE_CLINIC_NOW_UTC, _latest_bookable_utc() + timedelta(days=1)


def test_past_slots_are_excluded_from_availability() -> None:
    doctor = create_doctor()
    past_slot = create_availability_slot(
        doctor_id=doctor.id,
        start_time=REFERENCE_CLINIC_NOW_UTC - timedelta(hours=2),
        status=AvailabilitySlotStatus.AVAILABLE,
    )
    service = _build_scheduling_service(
        doctor=doctor,
        availability_slots=[past_slot],
    )

    slots = service.check_availability(
        doctor_id=doctor.id,
        start_from=_query_window()[0],
        start_to=_query_window()[1],
    )

    assert slots == []


def test_minimum_booking_lead_time_is_enforced() -> None:
    doctor = create_doctor()
    too_soon_slot = create_availability_slot(
        doctor_id=doctor.id,
        start_time=REFERENCE_CLINIC_NOW_UTC + timedelta(minutes=30),
        status=AvailabilitySlotStatus.AVAILABLE,
    )
    valid_slot = create_availability_slot(
        doctor_id=doctor.id,
        start_time=_earliest_bookable_utc() + timedelta(minutes=30),
        status=AvailabilitySlotStatus.AVAILABLE,
    )
    service = _build_scheduling_service(
        doctor=doctor,
        availability_slots=[too_soon_slot, valid_slot],
    )

    slots = service.check_availability(
        doctor_id=doctor.id,
        start_from=_query_window()[0],
        start_to=_query_window()[1],
    )

    assert [slot.id for slot in slots] == [valid_slot.id]


def test_booking_horizon_is_enforced() -> None:
    doctor = create_doctor()
    within_horizon_slot = create_availability_slot(
        doctor_id=doctor.id,
        start_time=_latest_bookable_utc() - timedelta(hours=1),
        status=AvailabilitySlotStatus.AVAILABLE,
    )
    beyond_horizon_slot = create_availability_slot(
        doctor_id=doctor.id,
        start_time=_latest_bookable_utc() + timedelta(days=1),
        status=AvailabilitySlotStatus.AVAILABLE,
    )
    service = _build_scheduling_service(
        doctor=doctor,
        availability_slots=[within_horizon_slot, beyond_horizon_slot],
    )

    slots = service.check_availability(
        doctor_id=doctor.id,
        start_from=_query_window()[0],
        start_to=_query_window()[1],
    )

    assert [slot.id for slot in slots] == [within_horizon_slot.id]


def test_non_available_db_statuses_are_excluded() -> None:
    doctor = create_doctor()
    bookable_start = _earliest_bookable_utc() + timedelta(hours=1)
    available_slot = create_availability_slot(
        doctor_id=doctor.id,
        start_time=bookable_start,
        status=AvailabilitySlotStatus.AVAILABLE,
    )
    booked_slot = create_availability_slot(
        doctor_id=doctor.id,
        start_time=bookable_start + timedelta(hours=1),
        status=AvailabilitySlotStatus.BOOKED,
    )
    held_slot = create_availability_slot(
        doctor_id=doctor.id,
        start_time=bookable_start + timedelta(hours=2),
        status=AvailabilitySlotStatus.HELD,
    )
    blocked_slot = create_availability_slot(
        doctor_id=doctor.id,
        start_time=bookable_start + timedelta(hours=3),
        status=AvailabilitySlotStatus.BLOCKED,
    )
    service = _build_scheduling_service(
        doctor=doctor,
        availability_slots=[available_slot, booked_slot, held_slot, blocked_slot],
    )

    slots = service.check_availability(
        doctor_id=doctor.id,
        start_from=_query_window()[0],
        start_to=_query_window()[1],
    )

    assert [slot.id for slot in slots] == [available_slot.id]


def test_redis_held_slot_is_excluded_from_availability() -> None:
    doctor = create_doctor()
    slot = create_availability_slot(
        doctor_id=doctor.id,
        start_time=_earliest_bookable_utc() + timedelta(hours=1),
        status=AvailabilitySlotStatus.AVAILABLE,
    )
    hold_repository = FakeAppointmentHoldRepository()
    hold_service = AppointmentHoldService(repository=hold_repository, ttl_seconds=300)
    hold_service.create_hold(
        availability_slot_id=slot.id,
        doctor_id=doctor.id,
        start_time=slot.start_time,
        end_time=slot.end_time,
        owner_id="other-call",
    )
    service = _build_scheduling_service(
        doctor=doctor,
        availability_slots=[slot],
        hold_service=hold_service,
    )

    slots = service.check_availability(
        doctor_id=doctor.id,
        start_from=_query_window()[0],
        start_to=_query_window()[1],
    )

    assert slots == []


def test_redis_unavailable_during_check_availability_degrades_gracefully(
    caplog: pytest.LogCaptureFixture,
) -> None:
    doctor = create_doctor()
    slot = create_availability_slot(
        doctor_id=doctor.id,
        start_time=_earliest_bookable_utc() + timedelta(hours=1),
        status=AvailabilitySlotStatus.AVAILABLE,
    )
    hold_service = AppointmentHoldService(
        repository=UnavailableHoldRepository(),
        ttl_seconds=300,
    )
    service = _build_scheduling_service(
        doctor=doctor,
        availability_slots=[slot],
        hold_service=hold_service,
    )

    with caplog.at_level(logging.WARNING, logger="app.services.scheduling"):
        slots = service.check_availability(
            doctor_id=doctor.id,
            start_from=_query_window()[0],
            start_to=_query_window()[1],
        )

    assert [slot.id for slot in slots] == [slot.id]
    assert any(
        "Skipping Redis hold filtering during availability lookup" in record.message
        for record in caplog.records
    )


def test_redis_unavailable_during_hold_fails_closed() -> None:
    service = AppointmentHoldService(
        repository=UnavailableHoldRepository(),
        ttl_seconds=300,
    )

    with pytest.raises(AppointmentHoldStoreUnavailableError):
        service.create_hold(
            availability_slot_id=uuid4(),
            doctor_id=uuid4(),
            start_time=_earliest_bookable_utc(),
            end_time=_earliest_bookable_utc() + timedelta(minutes=30),
            owner_id="call-123",
        )


def test_booking_requires_valid_hold() -> None:
    patient = Patient(
        id=uuid4(),
        full_name="John Miller",
        date_of_birth=date(1985, 4, 12),
        phone_number="+1-555-0201",
        email="john.miller@example.test",
    )
    specialty = create_specialty()
    doctor = create_doctor(specialty_id=specialty.id)
    slot = create_availability_slot(
        doctor_id=doctor.id,
        start_time=_earliest_bookable_utc() + timedelta(hours=1),
        status=AvailabilitySlotStatus.AVAILABLE,
    )
    hold_service = AppointmentHoldService(
        repository=FakeAppointmentHoldRepository(),
        ttl_seconds=300,
    )
    booking_service = AppointmentBookingService(
        patients=FakePatientRepository([patient]),
        doctors=FakeDoctorRepository([doctor]),
        availability_slots=FakeAvailabilitySlotRepository([slot]),
        appointments=FakeAppointmentRepository([]),
        hold_service=hold_service,
    )

    with pytest.raises(AppointmentHoldNotFoundError):
        booking_service.book_appointment(
            AppointmentBookingRequest(
                hold_id=uuid4(),
                availability_slot_id=slot.id,
                patient_id=patient.id,
                owner_id="call-123",
            ),
        )


def test_cancelled_appointment_slot_reappears_in_availability() -> None:
    patient = Patient(
        id=uuid4(),
        full_name="John Miller",
        date_of_birth=date(1985, 4, 12),
        phone_number="+1-555-0201",
        email="john.miller@example.test",
    )
    specialty = create_specialty()
    doctor = create_doctor(specialty_id=specialty.id)
    slot = create_availability_slot(
        doctor_id=doctor.id,
        start_time=_earliest_bookable_utc() + timedelta(hours=2),
        status=AvailabilitySlotStatus.BOOKED,
    )
    appointment = Appointment(
        id=uuid4(),
        patient_id=patient.id,
        doctor_id=doctor.id,
        specialty_id=specialty.id,
        availability_slot_id=slot.id,
        start_time=slot.start_time,
        end_time=slot.end_time,
        status=AppointmentStatus.SCHEDULED,
        reason="Annual checkup",
    )
    appointment_repository = FakeAppointmentRepository([appointment])
    availability_slot_repository = FakeAvailabilitySlotRepository([slot])
    cancellation_service = AppointmentCancellationService(
        appointments=appointment_repository,
        cancellation_attempts=FakeAppointmentCancellationAttemptRepository(),
        audit_logs=FakeAuditLogService(),  # type: ignore[arg-type]
        availability_slots=availability_slot_repository,
    )
    scheduling_service = SchedulingService(
        specialties=FakeSpecialtyRepository([]),
        doctors=FakeDoctorRepository([doctor]),
        patients=FakePatientRepository([patient]),
        availability_slots=availability_slot_repository,
        appointments=appointment_repository,
        clinic_time_service=make_test_clinic_time_service(),
        hold_service=None,
        availability_policy=_default_policy(),
    )

    cancellation_service.cancel_appointment(
        AppointmentCancellationRequest(
            appointment_id=appointment.id,
            explicit_confirmation=True,
            idempotency_key="cancel-1",
        ),
    )

    slots = scheduling_service.check_availability(
        doctor_id=doctor.id,
        start_from=_query_window()[0],
        start_to=_query_window()[1],
    )

    assert [item.id for item in slots] == [slot.id]
    assert slot.status == AvailabilitySlotStatus.AVAILABLE


def test_retell_check_availability_does_not_expose_redis_degradation(
    caplog: pytest.LogCaptureFixture,
) -> None:
    from app.adapters.retell.scheduling_tools import RetellSchedulingToolAdapter
    from app.schemas.retell_tools import RetellCheckAvailabilityRequest

    doctor = create_doctor()
    slot = create_availability_slot(
        doctor_id=doctor.id,
        start_time=_earliest_bookable_utc() + timedelta(hours=1),
        status=AvailabilitySlotStatus.AVAILABLE,
    )
    hold_service = AppointmentHoldService(
        repository=UnavailableHoldRepository(),
        ttl_seconds=300,
    )
    scheduling_service = _build_scheduling_service(
        doctor=doctor,
        availability_slots=[slot],
        hold_service=hold_service,
    )
    adapter = RetellSchedulingToolAdapter(scheduling_service)
    start_from, start_to = _query_window()

    with caplog.at_level(logging.WARNING, logger="app.services.scheduling"):
        response = adapter.check_availability(
            RetellCheckAvailabilityRequest(
                doctor_id=doctor.id,
                start_from=start_from,
                start_to=start_to,
            ),
        )

    assert response.ok is True
    assert response.result is not None
    result = cast(dict[str, Any], response.result)
    assert len(result["available_slots"]) == 1
    assert "redis" not in str(result).lower()
    assert "degrad" not in str(result).lower()


class UnavailableHoldRepository(FakeAppointmentHoldRepository):
    def create(self, hold: AppointmentHold, ttl_seconds: int) -> bool:
        raise RedisError("connection failed")

    def find_held_availability_slot_ids(
        self,
        *,
        doctor_id: UUID,
        slots: Sequence[tuple[UUID, datetime]],
    ) -> set[UUID]:
        raise RedisError("connection failed")
