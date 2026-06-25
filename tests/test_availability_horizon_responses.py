from __future__ import annotations

import logging
from collections.abc import Sequence
from datetime import UTC, date, datetime, timedelta
from typing import Any, cast
from uuid import uuid4
from zoneinfo import ZoneInfo

import pytest

from app.adapters.retell.scheduling_tools import RetellSchedulingToolAdapter
from app.domain.scheduling.availability import AvailabilityCheckStatus
from app.domain.scheduling.enums import AvailabilitySlotStatus
from app.domain.scheduling.expressions import DateExpressionKind
from app.models.scheduling import AvailabilitySlot, Doctor, Specialty
from app.schemas.retell_tools import (
    CheckAvailabilityToolArguments,
    RetellCheckAvailabilityRequest,
    RetellToolCallRequest,
)
from app.schemas.scheduling_expressions import DateExpressionSchema
from app.services.appointment_holds import AppointmentHoldService
from app.services.clinic_time import ClinicTimeService
from app.services.clock import FixedClock
from app.services.retell_tool_adapter import RetellToolCallingAdapter
from app.services.scheduling import SchedulingAvailabilityPolicy, SchedulingService
from app.services.scheduling_availability import SchedulingAvailabilityResolver
from tests.clinic_time_test_support import make_test_clinic_time_service
from tests.test_appointment_booking_service import FakeAppointmentRepository, FakePatientRepository
from tests.test_appointment_holds import FakeAppointmentHoldRepository
from tests.test_retell_tool_adapter import TrackingSchedulingService, TrackingVoiceCallRepository
from tests.test_scheduling_availability_hardening import UnavailableHoldRepository
from tests.test_scheduling_services import (
    FakeAvailabilitySlotRepository,
    FakeDoctorRepository,
    FakeSpecialtyRepository,
    create_availability_slot,
    create_doctor,
)


def _default_policy() -> SchedulingAvailabilityPolicy:
    return SchedulingAvailabilityPolicy(
        min_booking_lead_minutes=60,
        booking_horizon_days=14,
    )


def _build_scheduling_service(
    *,
    doctor: Doctor,
    availability_slots: Sequence[AvailabilitySlot],
    hold_service: AppointmentHoldService | None = None,
    clinic_time_service: ClinicTimeService | None = None,
    availability_policy: SchedulingAvailabilityPolicy | None = None,
) -> SchedulingService:
    return SchedulingService(
        specialties=FakeSpecialtyRepository([]),
        doctors=FakeDoctorRepository([doctor]),
        patients=FakePatientRepository([]),
        availability_slots=FakeAvailabilitySlotRepository(list(availability_slots)),
        appointments=FakeAppointmentRepository([]),
        clinic_time_service=clinic_time_service or make_test_clinic_time_service(),
        hold_service=hold_service,
        availability_policy=availability_policy or _default_policy(),
    )


def _june_end_clinic_time_service() -> ClinicTimeService:
    return ClinicTimeService(
        clinic_name="Demo Clinic",
        timezone="America/New_York",
        business_days="monday,tuesday,wednesday,thursday,friday",
        business_hours_start="09:00",
        business_hours_end="17:00",
        clock=FixedClock(
            current_time=datetime(2026, 6, 28, 14, 0, tzinfo=ZoneInfo("America/New_York")),
        ),
    )


def test_check_availability_returns_outside_booking_horizon() -> None:
    doctor = create_doctor()
    clinic_time = make_test_clinic_time_service()
    clinic_now = clinic_time.clinic_now()
    beyond_horizon_start = clinic_now + timedelta(days=15)
    service = _build_scheduling_service(
        doctor=doctor,
        availability_slots=[],
        clinic_time_service=clinic_time,
    )

    day_start = datetime.combine(
        beyond_horizon_start.date(),
        datetime.min.time(),
        tzinfo=clinic_time.timezone,
    ).astimezone(UTC)
    day_end = day_start + timedelta(days=1)

    result = service.check_availability_with_status(
        doctor_id=doctor.id,
        start_from=day_start,
        start_to=day_end,
    )

    assert result.status == AvailabilityCheckStatus.OUTSIDE_BOOKING_HORIZON
    assert result.available_slots == []
    assert result.booking_window is not None
    assert result.suggested_response_text is not None
    assert "open through" in result.suggested_response_text.lower()


def test_check_availability_returns_no_matching_slots() -> None:
    doctor = create_doctor()
    clinic_time = make_test_clinic_time_service()
    clinic_now = clinic_time.clinic_now()
    target_date = (clinic_now + timedelta(days=2)).date()
    day_start = datetime.combine(
        target_date,
        datetime.min.time(),
        tzinfo=clinic_time.timezone,
    ).astimezone(UTC)
    day_end = day_start + timedelta(days=1)
    service = _build_scheduling_service(doctor=doctor, availability_slots=[])

    result = service.check_availability_with_status(
        doctor_id=doctor.id,
        start_from=day_start,
        start_to=day_end,
    )

    assert result.status == AvailabilityCheckStatus.NO_MATCHING_SLOTS
    assert result.available_slots == []
    assert result.suggested_response_text is not None
    assert "not seeing any openings" in result.suggested_response_text.lower()


def test_next_month_resolved_date_inside_horizon_is_not_outside_horizon() -> None:
    doctor = create_doctor()
    clinic_time = _june_end_clinic_time_service()
    july_first_slot = create_availability_slot(
        doctor_id=doctor.id,
        start_time=datetime(2026, 7, 1, 14, 0, tzinfo=UTC),
        status=AvailabilitySlotStatus.AVAILABLE,
    )
    service = _build_scheduling_service(
        doctor=doctor,
        availability_slots=[july_first_slot],
        clinic_time_service=clinic_time,
    )
    resolver = SchedulingAvailabilityResolver(clinic_time)
    window = resolver.resolve_check_availability(
        CheckAvailabilityToolArguments(
            date_expression=DateExpressionSchema(
                kind=DateExpressionKind.EXACT_DATE,
                exact_date=date(2026, 7, 1),
            ),
        ),
        {},
    )
    assert window.is_resolved
    assert window.start_from is not None
    assert window.end_to is not None

    result = service.check_availability_with_status(
        doctor_id=doctor.id,
        start_from=window.start_from,
        start_to=window.end_to,
    )

    assert result.status == AvailabilityCheckStatus.AVAILABLE
    assert [slot.id for slot in result.available_slots] == [july_first_slot.id]


def test_retell_adapter_maps_outside_horizon_response_safely() -> None:
    doctor = create_doctor()
    clinic_time = make_test_clinic_time_service()
    clinic_now = clinic_time.clinic_now()
    beyond_horizon_start = clinic_now + timedelta(days=20)
    service = _build_scheduling_service(
        doctor=doctor,
        availability_slots=[],
        clinic_time_service=clinic_time,
    )
    adapter = RetellSchedulingToolAdapter(service)
    day_start = datetime.combine(
        beyond_horizon_start.date(),
        datetime.min.time(),
        tzinfo=clinic_time.timezone,
    ).astimezone(UTC)

    response = adapter.check_availability(
        RetellCheckAvailabilityRequest(
            doctor_id=doctor.id,
            start_from=day_start,
            start_to=day_start + timedelta(days=1),
        ),
    )

    assert response.ok is True
    result = cast(dict[str, Any], response.result)
    assert result["availability_status"] == "outside_booking_horizon"
    assert result["available_slots"] == []
    assert "booking_window" in result
    assert "horizon_days" not in str(result).lower()
    assert "redis" not in str(result).lower()


def test_retell_adapter_maps_no_matching_slots_response_safely() -> None:
    doctor = create_doctor()
    clinic_time = make_test_clinic_time_service()
    clinic_now = clinic_time.clinic_now()
    target_date = (clinic_now + timedelta(days=2)).date()
    service = _build_scheduling_service(
        doctor=doctor,
        availability_slots=[],
        clinic_time_service=clinic_time,
    )
    adapter = RetellSchedulingToolAdapter(service)
    day_start = datetime.combine(
        target_date,
        datetime.min.time(),
        tzinfo=clinic_time.timezone,
    ).astimezone(UTC)

    response = adapter.check_availability(
        RetellCheckAvailabilityRequest(
            doctor_id=doctor.id,
            start_from=day_start,
            start_to=day_start + timedelta(days=1),
        ),
    )

    assert response.ok is True
    result = cast(dict[str, Any], response.result)
    assert result["availability_status"] == "no_matching_slots"
    assert result["available_slots"] == []
    assert "suggested_response_text" in result
    assert "another day" in result["suggested_response_text"].lower()


def test_existing_availability_happy_paths_still_pass() -> None:
    doctor = create_doctor()
    clinic_time = make_test_clinic_time_service()
    clinic_now = clinic_time.clinic_now()
    slot_start = clinic_now + timedelta(hours=2)
    slot = create_availability_slot(
        doctor_id=doctor.id,
        start_time=slot_start,
        status=AvailabilitySlotStatus.AVAILABLE,
    )
    hold_service = AppointmentHoldService(
        repository=FakeAppointmentHoldRepository(),
        ttl_seconds=300,
    )
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
        clinic_time_service=clinic_time,
        hold_service=hold_service,
    )

    slots = service.check_availability(
        doctor_id=doctor.id,
        start_from=clinic_now,
        start_to=clinic_now + timedelta(days=14),
    )
    assert slots == []

    hold_service.repository.delete(doctor_id=doctor.id, start_time=slot.start_time)
    slots = service.check_availability(
        doctor_id=doctor.id,
        start_from=clinic_now,
        start_to=clinic_now + timedelta(days=14),
    )
    assert [item.id for item in slots] == [slot.id]

    result = service.check_availability_with_status(
        doctor_id=doctor.id,
        start_from=clinic_now,
        start_to=clinic_now + timedelta(days=14),
    )
    assert result.status == AvailabilityCheckStatus.AVAILABLE


def test_redis_unavailable_degrades_gracefully_with_status(
    caplog: pytest.LogCaptureFixture,
) -> None:
    doctor = create_doctor()
    clinic_time = make_test_clinic_time_service()
    clinic_now = clinic_time.clinic_now()
    slot = create_availability_slot(
        doctor_id=doctor.id,
        start_time=clinic_now + timedelta(hours=2),
        status=AvailabilitySlotStatus.AVAILABLE,
    )
    hold_service = AppointmentHoldService(
        repository=UnavailableHoldRepository(),
        ttl_seconds=300,
    )
    service = _build_scheduling_service(
        doctor=doctor,
        availability_slots=[slot],
        clinic_time_service=clinic_time,
        hold_service=hold_service,
    )

    with caplog.at_level(logging.WARNING, logger="app.services.scheduling"):
        result = service.check_availability_with_status(
            doctor_id=doctor.id,
            start_from=clinic_now,
            start_to=clinic_now + timedelta(days=14),
        )

    assert result.status == AvailabilityCheckStatus.AVAILABLE
    assert [item.id for item in result.available_slots] == [slot.id]


def test_retell_unified_adapter_returns_needs_date_clarification() -> None:
    specialty_id = uuid4()
    doctor_id = uuid4()
    specialty = Specialty(
        id=specialty_id,
        name="Dermatology",
        description="Skin care",
        is_active=True,
    )
    doctor = Doctor(
        id=doctor_id,
        specialty_id=specialty_id,
        full_name="Dr. Emily Carter",
        email="emily.carter@example-clinic.test",
        phone_number="+1-555-0101",
        is_active=True,
    )
    scheduling_service = TrackingSchedulingService(
        specialties=[specialty],
        doctors=[doctor],
        availability_slots=[],
    )
    adapter = RetellToolCallingAdapter(
        scheduling_service=scheduling_service,
        hold_service=AppointmentHoldService(
            repository=FakeAppointmentHoldRepository(),
            ttl_seconds=300,
        ),
        voice_calls=TrackingVoiceCallRepository(),
        clinic_time_service=make_test_clinic_time_service(),
    )

    response = adapter.execute(
        RetellToolCallRequest.model_validate(
            {
                "provider_call_id": "retell-vague-date",
                "tool_name": "check_availability",
                "arguments": {"specialty_name": "Dermatology"},
            },
        ),
    )

    assert response.status == "succeeded"
    assert response.result["availability_status"] == "needs_date_clarification"
    assert "specific day" in response.result["suggested_response_text"].lower()
