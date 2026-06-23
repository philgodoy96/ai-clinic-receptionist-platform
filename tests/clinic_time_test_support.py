from __future__ import annotations

from datetime import UTC, datetime

from app.services.clinic_time import ClinicTimeService
from app.services.clock import FixedClock

REFERENCE_CLINIC_NOW_UTC = datetime(2026, 7, 1, 14, 0, tzinfo=UTC)

# America/New_York is UTC-4 on 2026-07-01 (EDT).
CHECK_AVAILABILITY_LEGACY_START = "2026-07-01T13:00:00Z"
CHECK_AVAILABILITY_LEGACY_END = "2026-07-01T17:00:00Z"
BUSINESS_HOURS_SLOT_START_UTC = datetime(2026, 7, 1, 14, 0, tzinfo=UTC)


def make_test_clinic_time_service(
    *,
    clock: FixedClock | None = None,
) -> ClinicTimeService:
    return ClinicTimeService(
        clinic_name="Demo Clinic",
        timezone="America/New_York",
        business_days="monday,tuesday,wednesday,thursday,friday",
        business_hours_start="09:00",
        business_hours_end="17:00",
        clock=clock or FixedClock(current_time=REFERENCE_CLINIC_NOW_UTC),
    )
