"""Retell adapter flow tests for list_patient_appointments."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from typing import Any
from uuid import uuid4

import pytest

from app.domain.scheduling.enums import AppointmentStatus
from app.domain.voice_patient_intake import VoicePatientIntakeMode
from app.models.scheduling import Appointment, Doctor, Patient, Specialty
from app.repositories.memory.patient_resolution import InMemoryPatientResolutionRepository
from app.schemas.retell_tools import RetellToolCallRequest
from app.services.appointment_holds import AppointmentHoldService
from app.services.patient_identity_resolution import PatientIdentityResolutionService
from app.services.patient_intake import PatientIntakeService
from app.services.retell_tool_adapter import RetellToolCallingAdapter
from app.services.scheduling import SchedulingService
from app.services.voice_patient_appointment_lookup import VoicePatientAppointmentLookupService
from tests.clinic_time_test_support import make_test_clinic_time_service
from tests.test_appointment_booking_service import (
    FakeAppointmentRepository,
    FakeAvailabilitySlotRepository,
    FakeDoctorRepository,
)
from tests.test_patient_identity_resolution_service import FakePatientRepository
from tests.test_retell_tool_adapter import (
    TrackingAppointmentHoldRepository,
    TrackingVoiceCallRepository,
)
from tests.test_scheduling_services import FakeSpecialtyRepository

PROVIDER_CALL_ID = "retell-call-lookup-flow"


@pytest.fixture()
def lookup_flow_bundle() -> LookupFlowBundle:
    return create_lookup_flow_bundle()


class LookupFlowBundle:
    def __init__(self, *, adapter: RetellToolCallingAdapter, patient: Patient) -> None:
        self.adapter = adapter
        self.patient = patient


def create_lookup_flow_bundle() -> LookupFlowBundle:
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
    start_time = datetime(2026, 7, 7, 14, 0, tzinfo=UTC)
    appointment = Appointment(
        id=uuid4(),
        patient_id=patient.id,
        doctor_id=doctor.id,
        specialty_id=specialty.id,
        availability_slot_id=None,
        start_time=start_time,
        end_time=start_time + timedelta(minutes=30),
        status=AppointmentStatus.SCHEDULED,
    )
    patient_repository = FakePatientRepository([patient])
    appointment_repository = FakeAppointmentRepository([appointment])
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
    clinic_time_service = make_test_clinic_time_service()
    voice_patient_appointment_lookup = VoicePatientAppointmentLookupService(
        patient_identity_resolution=patient_identity_resolution,
        appointments=appointment_repository,
        scheduling_metadata=scheduling_service,
        clinic_time_service=clinic_time_service,
    )
    adapter = RetellToolCallingAdapter(
        scheduling_service=scheduling_service,
        hold_service=AppointmentHoldService(
            repository=TrackingAppointmentHoldRepository(),
            ttl_seconds=300,
        ),
        voice_calls=TrackingVoiceCallRepository(),
        patient_identity_resolution=patient_identity_resolution,
        voice_patient_appointment_lookup=voice_patient_appointment_lookup,
        clinic_time_service=clinic_time_service,
    )

    return LookupFlowBundle(adapter=adapter, patient=patient)


def _resolve_request(*, arguments: dict[str, Any]) -> RetellToolCallRequest:
    return RetellToolCallRequest.model_validate(
        {
            "provider_call_id": PROVIDER_CALL_ID,
            "tool_call_id": "resolve-lookup-flow",
            "tool_name": "resolve_patient_identity",
            "arguments": arguments,
        },
    )


def _list_appointments_request(
    *,
    patient_resolution_id: str,
    limit: int | None = None,
) -> RetellToolCallRequest:
    arguments: dict[str, Any] = {
        "patient_resolution_id": patient_resolution_id,
    }
    if limit is not None:
        arguments["limit"] = limit

    return RetellToolCallRequest.model_validate(
        {
            "provider_call_id": PROVIDER_CALL_ID,
            "tool_call_id": "list-lookup-flow",
            "tool_name": "list_patient_appointments",
            "arguments": arguments,
        },
    )


def test_list_patient_appointments_tool_succeeds_with_valid_resolution_id(
    lookup_flow_bundle: LookupFlowBundle,
) -> None:
    resolve_response = lookup_flow_bundle.adapter.execute(
        _resolve_request(
            arguments={
                "patient_name": lookup_flow_bundle.patient.full_name,
                "patient_date_of_birth": lookup_flow_bundle.patient.date_of_birth.isoformat(),
                "patient_email": lookup_flow_bundle.patient.email,
            },
        ),
    )
    resolution_id = resolve_response.result["patient_resolution_id"]
    assert resolution_id is not None

    response = lookup_flow_bundle.adapter.execute(
        _list_appointments_request(patient_resolution_id=resolution_id),
    )

    assert response.status == "succeeded"
    assert response.result["appointment_count"] == 1
    assert response.result["next_step"] == "confirm_appointment_selection"
    assert "appointments" in response.result
    appointment = response.result["appointments"][0]
    assert set(appointment) == {
        "appointment_id",
        "start_time",
        "end_time",
        "doctor_name",
        "specialty_name",
        "status",
        "human_readable_summary",
    }
    assert appointment["doctor_name"] == "Dr. Emily Carter"
    assert appointment["specialty_name"] == "Dermatology"
    assert appointment["status"] == "scheduled"
    assert "want to change" in response.result["suggested_response_text"]


def test_list_patient_appointments_tool_does_not_expose_patient_pii(
    lookup_flow_bundle: LookupFlowBundle,
) -> None:
    resolve_response = lookup_flow_bundle.adapter.execute(
        _resolve_request(
            arguments={
                "patient_name": lookup_flow_bundle.patient.full_name,
                "patient_date_of_birth": lookup_flow_bundle.patient.date_of_birth.isoformat(),
                "patient_email": lookup_flow_bundle.patient.email,
            },
        ),
    )
    resolution_id = resolve_response.result["patient_resolution_id"]
    assert resolution_id is not None

    response = lookup_flow_bundle.adapter.execute(
        _list_appointments_request(patient_resolution_id=resolution_id),
    )

    serialized = str(response.result)
    assert lookup_flow_bundle.patient.email not in serialized
    if lookup_flow_bundle.patient.phone_number is not None:
        assert lookup_flow_bundle.patient.phone_number not in serialized
    assert str(lookup_flow_bundle.patient.id) not in serialized
    assert "patient_id" not in response.result


def test_list_patient_appointments_tool_maps_invalid_token_to_safe_error(
    lookup_flow_bundle: LookupFlowBundle,
) -> None:
    response = lookup_flow_bundle.adapter.execute(
        _list_appointments_request(patient_resolution_id=str(uuid4())),
    )

    assert response.status == "failed"
    assert response.error_code == "patient_resolution_not_found"
    assert response.result["suggested_response_text"] == (
        "I need to verify your profile again before I can look up appointments."
    )
