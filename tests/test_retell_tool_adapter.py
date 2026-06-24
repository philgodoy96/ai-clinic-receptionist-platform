from __future__ import annotations

import inspect
from collections.abc import Sequence
from datetime import UTC, date, datetime, timedelta
from typing import Any
from uuid import UUID, uuid4

import pytest

from app.domain.retell_tools import RetellSupportedToolName
from app.domain.scheduling.appointment_holds import AppointmentHold
from app.domain.scheduling.enums import AppointmentStatus, AvailabilitySlotStatus
from app.domain.voice_calls.enums import VoiceCallStatus
from app.models.scheduling import Appointment, AvailabilitySlot, Doctor, Patient, Specialty
from app.models.voice_calls import VoiceCall
from app.schemas.retell_tools import RetellToolCallRequest
from app.services import retell_tool_adapter as retell_tool_adapter_module
from app.services.appointment_holds import AppointmentHoldService
from app.services.retell_tool_adapter import RetellToolCallingAdapter
from app.services.retell_tool_registry import (
    RETELL_TOOL_ALLOWLIST,
    SIDE_EFFECTING_RETELL_TOOLS,
)
from app.services.scheduling import (
    AvailabilitySlotNotFoundError,
    DoctorNotFoundError,
    PatientLookupCriteria,
)
from tests.clinic_time_test_support import make_test_clinic_time_service


@pytest.fixture()
def doctor_id() -> UUID:
    return uuid4()


@pytest.fixture()
def slot_id() -> UUID:
    return uuid4()


@pytest.fixture()
def availability_slot(doctor_id: UUID, slot_id: UUID) -> AvailabilitySlot:
    start_time = datetime(2026, 7, 1, 14, 0, tzinfo=UTC)

    return AvailabilitySlot(
        id=slot_id,
        doctor_id=doctor_id,
        start_time=start_time,
        end_time=start_time + timedelta(minutes=30),
        status=AvailabilitySlotStatus.AVAILABLE,
    )


@pytest.fixture()
def adapter_bundle(
    doctor_id: UUID,
    availability_slot: AvailabilitySlot,
) -> AdapterBundle:
    specialty_id = uuid4()
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
        availability_slots=[availability_slot],
    )
    hold_repository = TrackingAppointmentHoldRepository()
    hold_service = AppointmentHoldService(repository=hold_repository, ttl_seconds=300)
    voice_calls = TrackingVoiceCallRepository()
    booking_service = NeverCalledBookingService()
    email_service = NeverCalledEmailService()
    llm_service = NeverCalledLLMService()

    adapter = RetellToolCallingAdapter(
        scheduling_service=scheduling_service,
        hold_service=hold_service,
        voice_calls=voice_calls,
        clinic_time_service=make_test_clinic_time_service(),
    )

    return AdapterBundle(
        adapter=adapter,
        scheduling_service=scheduling_service,
        hold_repository=hold_repository,
        hold_service=hold_service,
        voice_calls=voice_calls,
        booking_service=booking_service,
        email_service=email_service,
        llm_service=llm_service,
        availability_slot=availability_slot,
        doctor_id=doctor_id,
    )


class AdapterBundle:
    def __init__(
        self,
        *,
        adapter: RetellToolCallingAdapter,
        scheduling_service: TrackingSchedulingService,
        hold_repository: TrackingAppointmentHoldRepository,
        hold_service: AppointmentHoldService,
        voice_calls: TrackingVoiceCallRepository,
        booking_service: NeverCalledBookingService,
        email_service: NeverCalledEmailService,
        llm_service: NeverCalledLLMService,
        availability_slot: AvailabilitySlot,
        doctor_id: UUID,
    ) -> None:
        self.adapter = adapter
        self.scheduling_service = scheduling_service
        self.hold_repository = hold_repository
        self.hold_service = hold_service
        self.voice_calls = voice_calls
        self.booking_service = booking_service
        self.email_service = email_service
        self.llm_service = llm_service
        self.availability_slot = availability_slot
        self.doctor_id = doctor_id


def test_explicit_tool_allowlist_only_includes_supported_tools() -> None:
    assert RETELL_TOOL_ALLOWLIST == frozenset(
        {
            RetellSupportedToolName.GET_CLINIC_CONTEXT,
            RetellSupportedToolName.CHECK_AVAILABILITY,
            RetellSupportedToolName.HOLD_APPOINTMENT_SLOT,
            RetellSupportedToolName.RELEASE_APPOINTMENT_HOLD,
            RetellSupportedToolName.BOOK_APPOINTMENT,
            RetellSupportedToolName.CANCEL_APPOINTMENT,
            RetellSupportedToolName.RESCHEDULE_APPOINTMENT,
            RetellSupportedToolName.RESOLVE_PATIENT_IDENTITY,
            RetellSupportedToolName.CONFIRM_PATIENT_IDENTITY,
        },
    )
    assert RetellSupportedToolName.GET_CLINIC_CONTEXT not in SIDE_EFFECTING_RETELL_TOOLS
    assert RetellSupportedToolName.CHECK_AVAILABILITY not in SIDE_EFFECTING_RETELL_TOOLS
    assert RetellSupportedToolName.HOLD_APPOINTMENT_SLOT in SIDE_EFFECTING_RETELL_TOOLS
    assert RetellSupportedToolName.RELEASE_APPOINTMENT_HOLD in SIDE_EFFECTING_RETELL_TOOLS
    assert RetellSupportedToolName.BOOK_APPOINTMENT in SIDE_EFFECTING_RETELL_TOOLS
    assert RetellSupportedToolName.CANCEL_APPOINTMENT in SIDE_EFFECTING_RETELL_TOOLS
    assert RetellSupportedToolName.RESCHEDULE_APPOINTMENT in SIDE_EFFECTING_RETELL_TOOLS
    assert RetellSupportedToolName.RESOLVE_PATIENT_IDENTITY in SIDE_EFFECTING_RETELL_TOOLS
    assert RetellSupportedToolName.CONFIRM_PATIENT_IDENTITY in SIDE_EFFECTING_RETELL_TOOLS


def test_adapter_dispatch_does_not_use_reflection() -> None:
    dispatch_source = inspect.getsource(RetellToolCallingAdapter._dispatch)
    module_source = inspect.getsource(retell_tool_adapter_module)

    assert "getattr(" not in dispatch_source
    assert "eval(" not in dispatch_source
    assert "exec(" not in dispatch_source
    assert "getattr(" not in module_source


def test_unknown_tool_rejected(adapter_bundle: AdapterBundle) -> None:
    response = adapter_bundle.adapter.execute(
        RetellToolCallRequest.model_validate(
            {
                "provider_call_id": "retell-call-123",
                "tool_name": "delete_appointment",
                "arguments": {},
            },
        ),
    )

    assert response.status == "rejected"
    assert response.error_code == "unsupported_retell_tool"
    assert adapter_bundle.scheduling_service.check_availability_calls == []
    assert adapter_bundle.hold_repository.create_calls == []


def test_adapter_dispatches_check_availability_to_scheduling_service(
    adapter_bundle: AdapterBundle,
) -> None:
    response = adapter_bundle.adapter.execute(
        RetellToolCallRequest.model_validate(
            {
                "provider_call_id": "retell-call-123",
                "tool_name": "check_availability",
                "arguments": {
                    "doctor_id": str(adapter_bundle.doctor_id),
                    "start_from": "2026-07-01T13:00:00Z",
                    "start_to": "2026-07-01T17:00:00Z",
                    "limit": 1,
                },
            },
        ),
    )

    assert response.status == "succeeded"
    assert len(adapter_bundle.scheduling_service.check_availability_calls) == 1
    assert adapter_bundle.scheduling_service.check_availability_calls[0].doctor_id == (
        adapter_bundle.doctor_id
    )
    assert len(response.result["available_slots"]) == 1


def test_adapter_dispatches_hold_to_existing_hold_service(
    adapter_bundle: AdapterBundle,
) -> None:
    response = adapter_bundle.adapter.execute(
        RetellToolCallRequest.model_validate(
            {
                "provider_call_id": "retell-call-456",
                "tool_call_id": "hold-call-1",
                "tool_name": "hold_appointment_slot",
                "arguments": {
                    "availability_slot_id": str(adapter_bundle.availability_slot.id),
                },
            },
        ),
    )

    assert response.status == "succeeded"
    assert len(adapter_bundle.hold_repository.create_calls) == 1
    assert adapter_bundle.hold_repository.create_calls[0].owner_id == "retell-call-456"
    assert "hold_id" in response.result


def test_adapter_dispatches_release_to_existing_hold_service(
    adapter_bundle: AdapterBundle,
) -> None:
    hold = AppointmentHold.create(
        availability_slot_id=adapter_bundle.availability_slot.id,
        doctor_id=adapter_bundle.availability_slot.doctor_id,
        start_time=adapter_bundle.availability_slot.start_time,
        end_time=adapter_bundle.availability_slot.end_time,
        owner_id="retell-call-789",
    )
    adapter_bundle.hold_repository.holds[(hold.doctor_id, hold.start_time)] = hold
    adapter_bundle.hold_repository.holds_by_id[hold.hold_id] = hold

    response = adapter_bundle.adapter.execute(
        RetellToolCallRequest.model_validate(
            {
                "provider_call_id": "retell-call-789",
                "tool_call_id": "release-call-1",
                "tool_name": "release_appointment_hold",
                "arguments": {
                    "hold_id": str(hold.hold_id),
                },
            },
        ),
    )

    assert response.status == "succeeded"
    assert adapter_bundle.hold_repository.delete_calls == [
        (hold.doctor_id, hold.start_time),
    ]


def test_duplicate_hold_tool_call_is_idempotent(adapter_bundle: AdapterBundle) -> None:
    request = RetellToolCallRequest.model_validate(
        {
            "provider_call_id": "retell-call-456",
            "tool_call_id": "hold-call-dup",
            "tool_name": "hold_appointment_slot",
            "arguments": {
                "availability_slot_id": str(adapter_bundle.availability_slot.id),
            },
        },
    )

    first = adapter_bundle.adapter.execute(request)
    second = adapter_bundle.adapter.execute(request)

    assert first.status == "succeeded"
    assert second.status == "succeeded"
    assert second.duplicate is True
    assert len(adapter_bundle.hold_repository.create_calls) == 1


def test_duplicate_release_tool_call_is_idempotent(adapter_bundle: AdapterBundle) -> None:
    hold = AppointmentHold.create(
        availability_slot_id=adapter_bundle.availability_slot.id,
        doctor_id=adapter_bundle.availability_slot.doctor_id,
        start_time=adapter_bundle.availability_slot.start_time,
        end_time=adapter_bundle.availability_slot.end_time,
        owner_id="retell-call-789",
    )
    adapter_bundle.hold_repository.holds[(hold.doctor_id, hold.start_time)] = hold
    adapter_bundle.hold_repository.holds_by_id[hold.hold_id] = hold

    request = RetellToolCallRequest.model_validate(
        {
            "provider_call_id": "retell-call-789",
            "tool_call_id": "release-call-dup",
            "tool_name": "release_appointment_hold",
            "arguments": {
                "hold_id": str(hold.hold_id),
            },
        },
    )

    first = adapter_bundle.adapter.execute(request)
    second = adapter_bundle.adapter.execute(request)

    assert first.status == "succeeded"
    assert second.status == "succeeded"
    assert second.duplicate is True
    assert len(adapter_bundle.hold_repository.delete_calls) == 1


def test_domain_failures_return_safe_failed_response(
    adapter_bundle: AdapterBundle,
) -> None:
    adapter_bundle.scheduling_service.check_availability_error = DoctorNotFoundError(
        "doctor was not found or is inactive",
    )

    response = adapter_bundle.adapter.execute(
        RetellToolCallRequest.model_validate(
            {
                "provider_call_id": "retell-call-123",
                "tool_name": "check_availability",
                "arguments": {
                    "doctor_id": str(adapter_bundle.doctor_id),
                    "start_from": "2026-07-01T13:00:00Z",
                    "start_to": "2026-07-01T17:00:00Z",
                },
            },
        ),
    )

    assert response.status == "failed"
    assert response.error_code == "doctor_not_found"
    assert response.result == {}


def test_no_booking_service_is_called(adapter_bundle: AdapterBundle) -> None:
    adapter_bundle.adapter.execute(
        RetellToolCallRequest.model_validate(
            {
                "provider_call_id": "retell-call-123",
                "tool_name": "check_availability",
                "arguments": {
                    "doctor_id": str(adapter_bundle.doctor_id),
                    "start_from": "2026-07-01T13:00:00Z",
                    "start_to": "2026-07-01T17:00:00Z",
                },
            },
        ),
    )

    assert adapter_bundle.booking_service.calls == []


def test_no_email_service_is_called(adapter_bundle: AdapterBundle) -> None:
    adapter_bundle.adapter.execute(
        RetellToolCallRequest.model_validate(
            {
                "provider_call_id": "retell-call-123",
                "tool_name": "hold_appointment_slot",
                "arguments": {
                    "availability_slot_id": str(adapter_bundle.availability_slot.id),
                },
            },
        ),
    )

    assert adapter_bundle.email_service.calls == []


def test_no_llm_service_is_called(adapter_bundle: AdapterBundle) -> None:
    adapter_bundle.adapter.execute(
        RetellToolCallRequest.model_validate(
            {
                "provider_call_id": "retell-call-123",
                "tool_name": "release_appointment_hold",
                "arguments": {
                    "hold_id": str(uuid4()),
                },
            },
        ),
    )

    assert adapter_bundle.llm_service.calls == []


def test_hold_appointment_slot_creates_only_hold_not_appointment(
    adapter_bundle: AdapterBundle,
) -> None:
    response = adapter_bundle.adapter.execute(
        RetellToolCallRequest.model_validate(
            {
                "provider_call_id": "retell-call-456",
                "tool_name": "hold_appointment_slot",
                "arguments": {
                    "availability_slot_id": str(adapter_bundle.availability_slot.id),
                },
            },
        ),
    )

    assert response.status == "succeeded"
    assert len(adapter_bundle.hold_repository.create_calls) == 1
    assert adapter_bundle.booking_service.calls == []
    assert adapter_bundle.scheduling_service.appointments == []


def test_release_appointment_hold_does_not_cancel_appointment(
    adapter_bundle: AdapterBundle,
) -> None:
    appointment = Appointment(
        id=uuid4(),
        patient_id=uuid4(),
        doctor_id=adapter_bundle.doctor_id,
        specialty_id=adapter_bundle.scheduling_service.specialties[0].id,
        availability_slot_id=adapter_bundle.availability_slot.id,
        start_time=adapter_bundle.availability_slot.start_time,
        end_time=adapter_bundle.availability_slot.end_time,
        status=AppointmentStatus.SCHEDULED,
        reason="Skin check",
    )
    adapter_bundle.scheduling_service.appointments = [appointment]

    hold = AppointmentHold.create(
        availability_slot_id=adapter_bundle.availability_slot.id,
        doctor_id=adapter_bundle.availability_slot.doctor_id,
        start_time=adapter_bundle.availability_slot.start_time,
        end_time=adapter_bundle.availability_slot.end_time,
        owner_id="retell-call-789",
    )
    adapter_bundle.hold_repository.holds[(hold.doctor_id, hold.start_time)] = hold
    adapter_bundle.hold_repository.holds_by_id[hold.hold_id] = hold

    response = adapter_bundle.adapter.execute(
        RetellToolCallRequest.model_validate(
            {
                "provider_call_id": "retell-call-789",
                "tool_name": "release_appointment_hold",
                "arguments": {
                    "hold_id": str(hold.hold_id),
                },
            },
        ),
    )

    assert response.status == "succeeded"
    assert len(adapter_bundle.scheduling_service.appointments) == 1
    assert adapter_bundle.scheduling_service.appointments[0].status == AppointmentStatus.SCHEDULED
    assert adapter_bundle.booking_service.calls == []


def test_check_availability_has_no_side_effects(adapter_bundle: AdapterBundle) -> None:
    adapter_bundle.adapter.execute(
        RetellToolCallRequest.model_validate(
            {
                "provider_call_id": "retell-call-123",
                "tool_name": "check_availability",
                "arguments": {
                    "doctor_id": str(adapter_bundle.doctor_id),
                    "start_from": "2026-07-01T13:00:00Z",
                    "start_to": "2026-07-01T17:00:00Z",
                },
            },
        ),
    )

    assert adapter_bundle.hold_repository.create_calls == []
    assert adapter_bundle.hold_repository.delete_calls == []
    assert adapter_bundle.voice_calls.outcomes == {}
    assert adapter_bundle.booking_service.calls == []


class TrackingSchedulingService:
    def __init__(
        self,
        *,
        specialties: list[Specialty],
        doctors: list[Doctor],
        availability_slots: list[AvailabilitySlot],
    ) -> None:
        self.specialties = specialties
        self.doctors = doctors
        self.availability_slots = availability_slots
        self.appointments: list[Appointment] = []
        self.check_availability_calls: list[Any] = []
        self.check_availability_error: Exception | None = None

    def list_specialties(self) -> Sequence[Specialty]:
        return self.specialties

    def list_doctors(self, specialty_id: UUID | None = None) -> Sequence[Doctor]:
        if specialty_id is None:
            return self.doctors

        return [doctor for doctor in self.doctors if doctor.specialty_id == specialty_id]

    def check_availability(
        self,
        *,
        doctor_id: UUID,
        start_from: datetime,
        start_to: datetime,
    ) -> Sequence[AvailabilitySlot]:
        self.check_availability_calls.append(
            SimpleNamespace(doctor_id=doctor_id, start_from=start_from, start_to=start_to),
        )

        if self.check_availability_error is not None:
            raise self.check_availability_error

        return [
            slot
            for slot in self.availability_slots
            if slot.doctor_id == doctor_id
            and slot.start_time >= start_from
            and slot.start_time < start_to
        ]

    def get_available_slot_for_hold(self, availability_slot_id: UUID) -> AvailabilitySlot:
        for slot in self.availability_slots:
            if slot.id == availability_slot_id:
                return slot

        raise AvailabilitySlotNotFoundError("availability slot was not found")

    def lookup_patient(self, criteria: PatientLookupCriteria) -> Patient | None:
        _ = criteria
        return None

    def list_upcoming_appointments(
        self,
        *,
        criteria: PatientLookupCriteria,
        start_from: datetime,
    ) -> Sequence[Appointment]:
        _ = criteria, start_from
        return []


class SimpleNamespace:
    def __init__(self, **kwargs: Any) -> None:
        for key, value in kwargs.items():
            setattr(self, key, value)


class TrackingAppointmentHoldRepository:
    def __init__(self) -> None:
        self.holds: dict[tuple[UUID, datetime], AppointmentHold] = {}
        self.holds_by_id: dict[UUID, AppointmentHold] = {}
        self.create_calls: list[AppointmentHold] = []
        self.delete_calls: list[tuple[UUID, datetime]] = []
        self.create_error: Exception | None = None

    def create(self, hold: AppointmentHold, ttl_seconds: int) -> bool:
        _ = ttl_seconds

        if self.create_error is not None:
            raise self.create_error

        key = (hold.doctor_id, hold.start_time)

        if key in self.holds:
            return False

        self.holds[key] = hold
        self.holds_by_id[hold.hold_id] = hold
        self.create_calls.append(hold)

        return True

    def get(self, *, doctor_id: UUID, start_time: datetime) -> AppointmentHold | None:
        return self.holds.get((doctor_id, start_time))

    def get_by_hold_id(self, hold_id: UUID) -> AppointmentHold | None:
        return self.holds_by_id.get(hold_id)

    def delete(self, *, doctor_id: UUID, start_time: datetime) -> None:
        hold = self.holds.pop((doctor_id, start_time), None)
        self.delete_calls.append((doctor_id, start_time))

        if hold is not None:
            self.holds_by_id.pop(hold.hold_id, None)


class TrackingVoiceCallRepository:
    def __init__(self) -> None:
        self.voice_calls: dict[str, VoiceCall] = {}
        self.outcomes: dict[str, dict[str, Any]] = {}

    def get_by_provider_call_id(
        self,
        *,
        provider: str,
        provider_call_id: str,
    ) -> VoiceCall | None:
        _ = provider
        return self.voice_calls.get(provider_call_id)

    def get_or_create_voice_call(
        self,
        *,
        provider: str,
        provider_call_id: str,
    ) -> tuple[VoiceCall, bool]:
        existing = self.get_by_provider_call_id(
            provider=provider,
            provider_call_id=provider_call_id,
        )

        if existing is not None:
            return existing, False

        voice_call = VoiceCall(
            id=uuid4(),
            provider=provider,
            provider_call_id=provider_call_id,
            status=VoiceCallStatus.CREATED,
        )
        self.voice_calls[provider_call_id] = voice_call

        return voice_call, True

    def get_tool_call_outcome_by_idempotency_key(
        self,
        *,
        idempotency_key: str,
    ) -> dict[str, Any] | None:
        return self.outcomes.get(idempotency_key)

    def record_tool_call_outcome(
        self,
        *,
        voice_call_id: UUID,
        provider: str,
        provider_call_id: str,
        event_type: str,
        tool_call_id: str,
        idempotency_key: str,
        outcome: dict[str, Any],
        occurred_at: datetime,
    ) -> bool:
        _ = (
            voice_call_id,
            provider,
            provider_call_id,
            event_type,
            tool_call_id,
            occurred_at,
        )

        if idempotency_key in self.outcomes:
            return False

        self.outcomes[idempotency_key] = outcome

        return True


class NeverCalledBookingService:
    def __init__(self) -> None:
        self.calls: list[str] = []

    def book_appointment(self, *_args: Any, **_kwargs: Any) -> None:
        self.calls.append("book_appointment")
        raise AssertionError("booking service must not be called")


class NeverCalledEmailService:
    def __init__(self) -> None:
        self.calls: list[str] = []

    def send(self, *_args: Any, **_kwargs: Any) -> None:
        self.calls.append("send")
        raise AssertionError("email service must not be called")


class NeverCalledLLMService:
    def __init__(self) -> None:
        self.calls: list[str] = []

    def complete(self, *_args: Any, **_kwargs: Any) -> None:
        self.calls.append("complete")
        raise AssertionError("llm service must not be called")


class PatientIdentityAdapterBundle:
    def __init__(self, *, adapter: RetellToolCallingAdapter, patients: list[Patient]) -> None:
        self.adapter = adapter
        self.patients = patients


@pytest.fixture()
def patient_identity_adapter_bundle() -> PatientIdentityAdapterBundle:
    from app.domain.voice_patient_intake import VoicePatientIntakeMode
    from app.repositories.memory.patient_resolution import InMemoryPatientResolutionRepository
    from app.services.patient_identity_resolution import PatientIdentityResolutionService
    from app.services.patient_intake import PatientIntakeService

    patients = [
        Patient(
            id=uuid4(),
            full_name="John Miller",
            date_of_birth=date(1985, 4, 12),
            phone_number="+1-555-0201",
            email="john.miller@example.test",
        ),
        Patient(
            id=uuid4(),
            full_name="Michael Lee Reed",
            date_of_birth=date(1988, 3, 15),
            phone_number=None,
            email="michael.lee.reed@example.test",
        ),
    ]
    repository = IdentityFakePatientRepository(patients)
    resolution_repository = InMemoryPatientResolutionRepository()
    patient_intake = PatientIntakeService(
        patients=repository,
        mode=VoicePatientIntakeMode.DEMO_AUTO_CREATE,
    )
    patient_identity_resolution = PatientIdentityResolutionService(
        patients=repository,
        resolutions=resolution_repository,
        patient_intake=patient_intake,
    )
    adapter = RetellToolCallingAdapter(
        scheduling_service=TrackingSchedulingService(
            specialties=[],
            doctors=[],
            availability_slots=[],
        ),
        hold_service=AppointmentHoldService(
            repository=TrackingAppointmentHoldRepository(),
            ttl_seconds=300,
        ),
        voice_calls=TrackingVoiceCallRepository(),
        patient_identity_resolution=patient_identity_resolution,
    )

    return PatientIdentityAdapterBundle(adapter=adapter, patients=patients)


class IdentityFakePatientRepository:
    def __init__(self, patients: Sequence[Patient]) -> None:
        self.patients = list(patients)

    def get_by_id(self, patient_id: UUID) -> Patient | None:
        for patient in self.patients:
            if patient.id == patient_id:
                return patient

        return None

    def get_by_email(self, email: str) -> Patient | None:
        normalized = email.strip().lower()
        for patient in self.patients:
            if patient.email.lower() == normalized:
                return patient

        return None

    def get_by_phone_number(self, phone_number: str) -> Patient | None:
        for patient in self.patients:
            if patient.phone_number == phone_number:
                return patient

        return None

    def get_by_identity(
        self,
        *,
        full_name: str,
        date_of_birth: date,
        phone_number: str | None = None,
        email: str | None = None,
    ) -> Patient | None:
        del phone_number, email
        for patient in self.patients:
            if patient.full_name == full_name and patient.date_of_birth == date_of_birth:
                return patient

        return None

    def list_by_date_of_birth(self, date_of_birth: date) -> list[Patient]:
        return [patient for patient in self.patients if patient.date_of_birth == date_of_birth]

    def add(self, patient: Patient) -> Patient:
        self.patients.append(patient)
        return patient


def _resolve_request(
    *,
    arguments: dict[str, Any],
    tool_call_id: str = "resolve-tool-1",
) -> RetellToolCallRequest:
    return RetellToolCallRequest.model_validate(
        {
            "provider_call_id": "retell-call-identity",
            "tool_call_id": tool_call_id,
            "tool_name": "resolve_patient_identity",
            "arguments": arguments,
        },
    )


def _confirm_request(
    *,
    patient_resolution_id: str,
    confirmed: bool,
    tool_call_id: str = "confirm-tool-1",
) -> RetellToolCallRequest:
    return RetellToolCallRequest.model_validate(
        {
            "provider_call_id": "retell-call-identity",
            "tool_call_id": tool_call_id,
            "tool_name": "confirm_patient_identity",
            "arguments": {
                "patient_resolution_id": patient_resolution_id,
                "confirmed": confirmed,
            },
        },
    )


def test_resolve_patient_identity_exact_match(
    patient_identity_adapter_bundle: PatientIdentityAdapterBundle,
) -> None:
    response = patient_identity_adapter_bundle.adapter.execute(
        _resolve_request(
            arguments={
                "patient_name": "John Miller",
                "patient_date_of_birth": "1985-04-12",
            },
        ),
    )

    assert response.status == "succeeded"
    assert response.result["match_status"] == "exact_match"
    assert response.result["requires_confirmation"] is False
    assert response.result["patient_resolution_id"] is not None
    assert "patient_id" not in response.result


def test_resolve_patient_identity_possible_match_requires_confirmation(
    patient_identity_adapter_bundle: PatientIdentityAdapterBundle,
) -> None:
    response = patient_identity_adapter_bundle.adapter.execute(
        _resolve_request(
            arguments={
                "patient_name": "Michael Reed",
                "patient_date_of_birth": "1988-03-15",
                "patient_email": "michael.lee.reed@example.test",
            },
        ),
    )

    assert response.status == "succeeded"
    assert response.result["match_status"] == "possible_match"
    assert response.result["requires_confirmation"] is True
    assert response.result["confirmation_question"] is not None


def test_confirm_patient_identity_confirms_possible_match(
    patient_identity_adapter_bundle: PatientIdentityAdapterBundle,
) -> None:
    resolve_response = patient_identity_adapter_bundle.adapter.execute(
        _resolve_request(
            arguments={
                "patient_name": "Michael Reed",
                "patient_date_of_birth": "1988-03-15",
                "patient_email": "michael.lee.reed@example.test",
            },
        ),
    )
    resolution_id = resolve_response.result["patient_resolution_id"]

    confirm_response = patient_identity_adapter_bundle.adapter.execute(
        _confirm_request(
            patient_resolution_id=resolution_id,
            confirmed=True,
            tool_call_id="confirm-tool-2",
        ),
    )

    assert confirm_response.status == "succeeded"
    assert confirm_response.result["confirmed"] is True
    assert confirm_response.result["requires_confirmation"] is False


def test_confirm_patient_identity_rejects_possible_match(
    patient_identity_adapter_bundle: PatientIdentityAdapterBundle,
) -> None:
    resolve_response = patient_identity_adapter_bundle.adapter.execute(
        _resolve_request(
            arguments={
                "patient_name": "Michael Reed",
                "patient_date_of_birth": "1988-03-15",
                "patient_email": "michael.lee.reed@example.test",
            },
        ),
    )
    resolution_id = resolve_response.result["patient_resolution_id"]

    confirm_response = patient_identity_adapter_bundle.adapter.execute(
        _confirm_request(
            patient_resolution_id=resolution_id,
            confirmed=False,
            tool_call_id="confirm-tool-3",
        ),
    )

    assert confirm_response.status == "rejected"
    assert confirm_response.error_code == "patient_identity_confirmation_rejected"


def test_resolve_patient_identity_not_found(
    patient_identity_adapter_bundle: PatientIdentityAdapterBundle,
) -> None:
    response = patient_identity_adapter_bundle.adapter.execute(
        _resolve_request(
            arguments={
                "patient_name": "Unknown Patient",
                "patient_date_of_birth": "1990-01-01",
                "caller_claims_existing_patient": True,
                "allow_demo_patient_creation": False,
            },
            tool_call_id="resolve-tool-not-found",
        ),
    )

    assert response.status == "succeeded"
    assert response.result["match_status"] == "not_found"
    assert response.result["patient_resolution_id"] is None


def test_resolve_patient_identity_created_when_demo_creation_allowed(
    patient_identity_adapter_bundle: PatientIdentityAdapterBundle,
) -> None:
    response = patient_identity_adapter_bundle.adapter.execute(
        _resolve_request(
            arguments={
                "patient_name": "Felipe Logan",
                "patient_date_of_birth": "1995-11-02",
                "patient_email": "felipe.logan@example.test",
                "caller_claims_existing_patient": False,
                "allow_demo_patient_creation": True,
            },
            tool_call_id="resolve-tool-created",
        ),
    )

    assert response.status == "succeeded"
    assert response.result["match_status"] == "created"
    assert response.result["patient_resolution_id"] is not None


def test_resolve_patient_identity_invalid_args_rejected(
    patient_identity_adapter_bundle: PatientIdentityAdapterBundle,
) -> None:
    response = patient_identity_adapter_bundle.adapter.execute(
        RetellToolCallRequest.model_validate(
            {
                "provider_call_id": "retell-call-identity",
                "tool_name": "resolve_patient_identity",
                "arguments": {
                    "patient_name": " ",
                    "patient_date_of_birth": "1985-04-12",
                },
            },
        ),
    )

    assert response.status == "rejected"
    assert response.error_code == "retell_tool_arguments_invalid"


def test_confirm_patient_identity_unknown_token_fails(
    patient_identity_adapter_bundle: PatientIdentityAdapterBundle,
) -> None:
    response = patient_identity_adapter_bundle.adapter.execute(
        _confirm_request(
            patient_resolution_id="00000000-0000-4000-8000-000000000099",
            confirmed=True,
            tool_call_id="confirm-tool-unknown",
        ),
    )

    assert response.status == "failed"
    assert response.error_code == "patient_resolution_not_found"
