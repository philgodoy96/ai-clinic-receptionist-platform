from __future__ import annotations

from datetime import date
from uuid import uuid4

import pytest

from app.domain.voice_patient_intake import (
    PatientIntakeIdentity,
    PatientIntakeNotFoundError,
    VoicePatientIntakeMode,
)
from app.models.scheduling import Patient
from app.services.patient_intake import PatientIntakeService
from tests.test_appointment_booking_service import FakePatientRepository


def _identity(
    *,
    full_name: str = "Ava Thompson",
    email: str = "ava.thompson@example.test",
    phone_number: str | None = None,
) -> PatientIntakeIdentity:
    return PatientIntakeIdentity(
        full_name=full_name,
        date_of_birth=date(1992, 9, 3),
        email=email,
        phone_number=phone_number,
    )


def test_existing_patient_lookup_succeeds_in_lookup_only_mode() -> None:
    existing = Patient(
        id=uuid4(),
        full_name="John Miller",
        date_of_birth=date(1985, 4, 12),
        phone_number="+1-555-0201",
        email="john.miller@example.test",
    )
    service = PatientIntakeService(
        patients=FakePatientRepository([existing]),
        mode=VoicePatientIntakeMode.LOOKUP_ONLY,
    )

    patient = service.resolve_for_voice_booking(
        PatientIntakeIdentity(
            full_name="John Miller",
            date_of_birth=date(1985, 4, 12),
            email="JOHN.MILLER@EXAMPLE.TEST",
        ),
    )

    assert patient.id == existing.id


def test_new_patient_fails_in_lookup_only_mode() -> None:
    service = PatientIntakeService(
        patients=FakePatientRepository([]),
        mode=VoicePatientIntakeMode.LOOKUP_ONLY,
    )

    with pytest.raises(PatientIntakeNotFoundError):
        service.resolve_for_voice_booking(_identity())


def test_new_demo_patient_succeeds_in_demo_auto_create_mode() -> None:
    repository = FakePatientRepository([])
    service = PatientIntakeService(
        patients=repository,
        mode=VoicePatientIntakeMode.DEMO_AUTO_CREATE,
    )

    patient = service.resolve_for_voice_booking(_identity())

    assert len(repository.patients) == 1
    assert patient.full_name == "Ava Thompson"
    assert patient.email == "ava.thompson@example.test"
    assert patient.phone_number is None


def test_demo_auto_create_stores_caller_provided_phone_only() -> None:
    repository = FakePatientRepository([])
    service = PatientIntakeService(
        patients=repository,
        mode=VoicePatientIntakeMode.DEMO_AUTO_CREATE,
    )

    patient = service.resolve_for_voice_booking(
        _identity(phone_number="+1-555-9999"),
    )

    assert patient.phone_number == "+1-555-9999"


def test_demo_auto_create_does_not_invent_phone_when_omitted() -> None:
    repository = FakePatientRepository([])
    service = PatientIntakeService(
        patients=repository,
        mode=VoicePatientIntakeMode.DEMO_AUTO_CREATE,
    )

    patient = service.resolve_for_voice_booking(
        _identity(
            full_name="Felipe Logan",
            email="felipe.logan@example.test",
        ),
    )

    assert patient.email == "felipe.logan@example.test"
    assert patient.phone_number is None


def test_demo_auto_create_rejects_non_sample_email_domain() -> None:
    service = PatientIntakeService(
        patients=FakePatientRepository([]),
        mode=VoicePatientIntakeMode.DEMO_AUTO_CREATE,
    )

    with pytest.raises(PatientIntakeNotFoundError):
        service.resolve_for_voice_booking(
            _identity(email="ava.thompson@example.com"),
        )


def test_demo_auto_create_is_idempotent_for_retries() -> None:
    repository = FakePatientRepository([])
    service = PatientIntakeService(
        patients=repository,
        mode=VoicePatientIntakeMode.DEMO_AUTO_CREATE,
    )
    identity = _identity()

    first = service.resolve_for_voice_booking(identity)
    second = service.resolve_for_voice_booking(identity)

    assert first.id == second.id
    assert len(repository.patients) == 1


def test_demo_auto_create_normalizes_name_and_email() -> None:
    repository = FakePatientRepository([])
    service = PatientIntakeService(
        patients=repository,
        mode=VoicePatientIntakeMode.DEMO_AUTO_CREATE,
    )

    patient = service.resolve_for_voice_booking(
        PatientIntakeIdentity(
            full_name="  Ava   Thompson ",
            date_of_birth=date(1992, 9, 3),
            email="  AVA.THOMPSON@EXAMPLE.TEST ",
        ),
    )

    assert patient.full_name == "Ava Thompson"
    assert patient.email == "ava.thompson@example.test"
