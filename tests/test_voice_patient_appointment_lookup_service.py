"""Unit tests for voice patient appointment lookup."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, date, datetime, timedelta
from uuid import UUID, uuid4

import pytest

from app.domain.patient_identity_resolution import PatientIdentityResolutionRequest
from app.domain.scheduling.enums import AppointmentStatus
from app.domain.voice_appointment_lookup import (
    ListPatientAppointmentsRequest,
    PatientAppointmentLookupNextStep,
    PatientResolutionRequiredForAppointmentLookupError,
)
from app.domain.voice_patient_intake import VoicePatientIntakeMode
from app.models.scheduling import Appointment, Doctor, Patient, Specialty
from app.repositories.memory.patient_resolution import InMemoryPatientResolutionRepository
from app.services.patient_identity_resolution import PatientIdentityResolutionService
from app.services.patient_intake import PatientIntakeService
from app.services.scheduling import SchedulingService
from app.services.voice_patient_appointment_lookup import VoicePatientAppointmentLookupService
from tests.clinic_time_test_support import REFERENCE_CLINIC_NOW_UTC, make_test_clinic_time_service
from tests.test_appointment_booking_service import (
    FakeAppointmentRepository,
    FakeAvailabilitySlotRepository,
    FakeDoctorRepository,
)
from tests.test_patient_identity_resolution_service import FakePatientRepository
from tests.test_scheduling_services import FakeSpecialtyRepository

PROVIDER_CALL_ID = "retell-call-lookup-service"


@pytest.fixture()
def lookup_context() -> LookupContext:
    return create_lookup_context()


class LookupContext:
    def __init__(
        self,
        *,
        service: VoicePatientAppointmentLookupService,
        patient_identity_resolution: PatientIdentityResolutionService,
        patient: Patient,
        specialty: Specialty,
        doctor: Doctor,
        appointments: FakeAppointmentRepository,
    ) -> None:
        self.service = service
        self.patient_identity_resolution = patient_identity_resolution
        self.patient = patient
        self.specialty = specialty
        self.doctor = doctor
        self.appointments = appointments


def create_lookup_context(
    *,
    appointments: Sequence[Appointment] | None = None,
) -> LookupContext:
    specialty = Specialty(
        id=uuid4(),
        name="Dermatology",
        description="Skin care",
        is_active=True,
    )
    doctor = Doctor(
        id=uuid4(),
        specialty_id=specialty.id,
        full_name="Dr. Emily Carter",
        email="emily.carter@example-clinic.test",
        phone_number="+1-555-0101",
        is_active=True,
    )
    patient = Patient(
        id=uuid4(),
        full_name="John Miller",
        date_of_birth=date(1985, 4, 12),
        phone_number="+1-555-0201",
        email="john.miller@example.test",
    )
    patient_repository = FakePatientRepository([patient])
    appointment_repository = FakeAppointmentRepository(list(appointments or []))
    scheduling_service = SchedulingService(
        specialties=FakeSpecialtyRepository([specialty]),
        doctors=FakeDoctorRepository([doctor]),
        patients=patient_repository,
        availability_slots=FakeAvailabilitySlotRepository([]),
        appointments=appointment_repository,
    )
    patient_identity_resolution = PatientIdentityResolutionService(
        patients=patient_repository,
        resolutions=InMemoryPatientResolutionRepository(),
        patient_intake=PatientIntakeService(
            patients=patient_repository,
            mode=VoicePatientIntakeMode.LOOKUP_ONLY,
        ),
    )
    service = VoicePatientAppointmentLookupService(
        patient_identity_resolution=patient_identity_resolution,
        appointments=appointment_repository,
        scheduling_metadata=scheduling_service,
        clinic_time_service=make_test_clinic_time_service(),
    )

    return LookupContext(
        service=service,
        patient_identity_resolution=patient_identity_resolution,
        patient=patient,
        specialty=specialty,
        doctor=doctor,
        appointments=appointment_repository,
    )


def _appointment(
    *,
    patient_id: UUID,
    doctor_id: UUID,
    specialty_id: UUID,
    start_time: datetime,
    status: AppointmentStatus = AppointmentStatus.SCHEDULED,
) -> Appointment:
    return Appointment(
        id=uuid4(),
        patient_id=patient_id,
        doctor_id=doctor_id,
        specialty_id=specialty_id,
        availability_slot_id=None,
        start_time=start_time,
        end_time=start_time + timedelta(minutes=30),
        status=status,
    )


def _resolution_id(
    context: LookupContext,
    *,
    provider_call_id: str = PROVIDER_CALL_ID,
) -> str:
    result = context.patient_identity_resolution.resolve(
        PatientIdentityResolutionRequest(
            patient_name=context.patient.full_name,
            patient_date_of_birth=context.patient.date_of_birth,
            patient_email=context.patient.email,
            provider_call_id=provider_call_id,
        ),
    )
    assert result.patient_resolution_id is not None
    return result.patient_resolution_id


def test_list_patient_appointments_returns_zero_when_none_upcoming(
    lookup_context: LookupContext,
) -> None:
    resolution_id = _resolution_id(lookup_context)

    result = lookup_context.service.list_patient_appointments(
        ListPatientAppointmentsRequest(
            patient_resolution_id=resolution_id,
            provider_call_id=PROVIDER_CALL_ID,
        ),
    )

    assert result.appointment_count == 0
    assert result.appointments == []
    assert result.next_step is PatientAppointmentLookupNextStep.NO_UPCOMING_APPOINTMENTS
    assert "scheduling a new appointment" in result.suggested_response_text


def test_list_patient_appointments_returns_one_upcoming_appointment(
    lookup_context: LookupContext,
) -> None:
    start_time = datetime(2026, 7, 7, 14, 0, tzinfo=UTC)
    lookup_context.appointments.appointments.append(
        _appointment(
            patient_id=lookup_context.patient.id,
            doctor_id=lookup_context.doctor.id,
            specialty_id=lookup_context.specialty.id,
            start_time=start_time,
        ),
    )
    resolution_id = _resolution_id(lookup_context)

    result = lookup_context.service.list_patient_appointments(
        ListPatientAppointmentsRequest(
            patient_resolution_id=resolution_id,
            provider_call_id=PROVIDER_CALL_ID,
        ),
    )

    assert result.appointment_count == 1
    assert result.next_step is PatientAppointmentLookupNextStep.CONFIRM_APPOINTMENT_SELECTION
    assert len(result.appointments) == 1
    summary = result.appointments[0]
    assert summary.doctor_name == "Dr. Emily Carter"
    assert summary.specialty_name == "Dermatology"
    assert summary.status == "scheduled"
    assert "Dermatology with Dr. Emily Carter" in summary.human_readable_summary
    assert "Eastern" in summary.human_readable_summary
    assert "want to change" in result.suggested_response_text


def test_list_patient_appointments_returns_multiple_ordered_soonest_first(
    lookup_context: LookupContext,
) -> None:
    later = datetime(2026, 7, 9, 18, 0, tzinfo=UTC)
    sooner = datetime(2026, 7, 7, 14, 0, tzinfo=UTC)
    lookup_context.appointments.appointments.extend(
        [
            _appointment(
                patient_id=lookup_context.patient.id,
                doctor_id=lookup_context.doctor.id,
                specialty_id=lookup_context.specialty.id,
                start_time=later,
            ),
            _appointment(
                patient_id=lookup_context.patient.id,
                doctor_id=lookup_context.doctor.id,
                specialty_id=lookup_context.specialty.id,
                start_time=sooner,
            ),
        ],
    )
    resolution_id = _resolution_id(lookup_context)

    result = lookup_context.service.list_patient_appointments(
        ListPatientAppointmentsRequest(
            patient_resolution_id=resolution_id,
            provider_call_id=PROVIDER_CALL_ID,
        ),
    )

    assert result.appointment_count == 2
    assert result.next_step is PatientAppointmentLookupNextStep.CHOOSE_APPOINTMENT
    assert "Tuesday" in result.appointments[0].human_readable_summary
    assert "Thursday" in result.appointments[1].human_readable_summary
    assert "Which one would you like to change?" in result.suggested_response_text


def test_list_patient_appointments_rejects_invalid_resolution_id(
    lookup_context: LookupContext,
) -> None:
    with pytest.raises(PatientResolutionRequiredForAppointmentLookupError):
        lookup_context.service.list_patient_appointments(
            ListPatientAppointmentsRequest(
                patient_resolution_id=str(uuid4()),
                provider_call_id=PROVIDER_CALL_ID,
            ),
        )


def test_list_patient_appointments_rejects_owner_call_mismatch(
    lookup_context: LookupContext,
) -> None:
    resolution_id = _resolution_id(lookup_context, provider_call_id="retell-call-owner-a")

    with pytest.raises(PatientResolutionRequiredForAppointmentLookupError):
        lookup_context.service.list_patient_appointments(
            ListPatientAppointmentsRequest(
                patient_resolution_id=resolution_id,
                provider_call_id="retell-call-owner-b",
            ),
        )


def test_list_patient_appointments_excludes_past_appointments() -> None:
    context = create_lookup_context()
    context.appointments.appointments.append(
        _appointment(
            patient_id=context.patient.id,
            doctor_id=context.doctor.id,
            specialty_id=context.specialty.id,
            start_time=REFERENCE_CLINIC_NOW_UTC - timedelta(days=1),
        ),
    )
    resolution_id = _resolution_id(context)

    result = context.service.list_patient_appointments(
        ListPatientAppointmentsRequest(
            patient_resolution_id=resolution_id,
            provider_call_id=PROVIDER_CALL_ID,
        ),
    )

    assert result.appointment_count == 0


def test_list_patient_appointments_excludes_non_scheduled_statuses() -> None:
    context = create_lookup_context()
    future_start = datetime(2026, 7, 7, 14, 0, tzinfo=UTC)
    for status in (
        AppointmentStatus.CANCELLED,
        AppointmentStatus.COMPLETED,
        AppointmentStatus.RESCHEDULED,
    ):
        context.appointments.appointments.append(
            _appointment(
                patient_id=context.patient.id,
                doctor_id=context.doctor.id,
                specialty_id=context.specialty.id,
                start_time=future_start,
                status=status,
            ),
        )
    resolution_id = _resolution_id(context)

    result = context.service.list_patient_appointments(
        ListPatientAppointmentsRequest(
            patient_resolution_id=resolution_id,
            provider_call_id=PROVIDER_CALL_ID,
        ),
    )

    assert result.appointment_count == 0


def test_list_patient_appointments_respects_limit() -> None:
    context = create_lookup_context()
    for day_offset in (7, 8, 9):
        context.appointments.appointments.append(
            _appointment(
                patient_id=context.patient.id,
                doctor_id=context.doctor.id,
                specialty_id=context.specialty.id,
                start_time=datetime(2026, 7, day_offset, 14, 0, tzinfo=UTC),
            ),
        )
    resolution_id = _resolution_id(context)

    result = context.service.list_patient_appointments(
        ListPatientAppointmentsRequest(
            patient_resolution_id=resolution_id,
            provider_call_id=PROVIDER_CALL_ID,
            limit=2,
        ),
    )

    assert result.appointment_count == 2
    assert len(result.appointments) == 2
