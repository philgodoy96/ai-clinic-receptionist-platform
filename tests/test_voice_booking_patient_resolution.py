from __future__ import annotations

from dataclasses import replace
from datetime import date
from uuid import UUID, uuid4

import pytest

from app.domain.patient_identity_resolution import (
    PatientResolutionMatchStatus,
    PatientResolutionRecord,
)
from app.domain.voice_booking import (
    VoiceBookingIdentityConfirmationRequiredError,
    VoiceBookingIdentityNotResolvedError,
    VoiceBookingMissingConfirmationError,
    VoiceBookingMissingHoldError,
    VoiceBookingResolutionIdRequiredError,
)
from app.domain.voice_patient_intake import VoicePatientIntakeMode
from app.models.scheduling import Patient
from app.repositories.memory.patient_resolution import InMemoryPatientResolutionRepository
from app.services.patient_identity_resolution import PatientIdentityResolutionService
from app.services.patient_intake import PatientIntakeService
from tests.test_appointment_booking_service import FakePatientRepository
from tests.test_voice_booking_confirmation_service import (
    VoiceBookingConfirmationContext,
    _active_hold_id,
    _build_request,
    create_voice_booking_confirmation_context,
)


def _patient_identity_resolution_service(
    patients: list[Patient],
) -> tuple[PatientIdentityResolutionService, InMemoryPatientResolutionRepository]:
    repository = FakePatientRepository(patients)
    resolution_repository = InMemoryPatientResolutionRepository()
    service = PatientIdentityResolutionService(
        patients=repository,
        resolutions=resolution_repository,
        patient_intake=PatientIntakeService(
            patients=repository,
            mode=VoicePatientIntakeMode.DEMO_AUTO_CREATE,
        ),
    )
    return service, resolution_repository


def _store_resolution(
    *,
    resolution_repository: InMemoryPatientResolutionRepository,
    patient: Patient,
    match_status: PatientResolutionMatchStatus,
    provider_call_id: str = "retell-call-123",
    conversation_id: UUID | None = None,
    confirmed: bool = False,
) -> str:
    record = PatientResolutionRecord.create(
        patient_id=patient.id,
        match_status=match_status,
        provider_call_id=provider_call_id,
        conversation_id=conversation_id,
        confirmed=confirmed,
    )
    resolution_repository.save(record, ttl_seconds=300)
    return str(record.resolution_id)


def _booking_context_with_resolution(
    *,
    patients: list[Patient] | None = None,
) -> tuple[VoiceBookingConfirmationContext, PatientIdentityResolutionService, list[Patient]]:
    seeded_patients = patients or []
    resolution_service, _ = _patient_identity_resolution_service(seeded_patients)
    context = create_voice_booking_confirmation_context(
        patients=seeded_patients or None,
        patient_identity_resolution=resolution_service,
    )
    if not seeded_patients:
        seeded_patients = [context.booking_context.patient]
    return context, resolution_service, seeded_patients


def test_booking_succeeds_with_exact_match_resolution_token() -> None:
    context, resolution_service, patients = _booking_context_with_resolution()
    patient = patients[0]
    _, resolution_repository = _patient_identity_resolution_service(patients)
    context.service.patient_identity_resolution = PatientIdentityResolutionService(
        patients=FakePatientRepository(patients),
        resolutions=resolution_repository,
        patient_intake=PatientIntakeService(
            patients=FakePatientRepository(patients),
            mode=VoicePatientIntakeMode.LOOKUP_ONLY,
        ),
    )
    resolution_id = _store_resolution(
        resolution_repository=resolution_repository,
        patient=patient,
        match_status=PatientResolutionMatchStatus.EXACT_MATCH,
        conversation_id=context.conversation.id,
        confirmed=True,
    )
    hold_id = _active_hold_id(context)

    result = context.service.confirm_and_book(
        replace(
            _build_request(context, hold_id=hold_id),
            patient_resolution_id=resolution_id,
        ),
    )

    assert result.patient_id == patient.id
    assert len(context.tracking_booking.book_calls) == 1


def test_booking_succeeds_with_confirmed_possible_match_token() -> None:
    context, _, patients = _booking_context_with_resolution()
    patient = patients[0]
    _, resolution_repository = _patient_identity_resolution_service(patients)
    context.service.patient_identity_resolution = PatientIdentityResolutionService(
        patients=FakePatientRepository(patients),
        resolutions=resolution_repository,
        patient_intake=PatientIntakeService(
            patients=FakePatientRepository(patients),
            mode=VoicePatientIntakeMode.LOOKUP_ONLY,
        ),
    )
    resolution_id = _store_resolution(
        resolution_repository=resolution_repository,
        patient=patient,
        match_status=PatientResolutionMatchStatus.POSSIBLE_MATCH,
        conversation_id=context.conversation.id,
        confirmed=True,
    )
    hold_id = _active_hold_id(context)

    result = context.service.confirm_and_book(
        replace(
            _build_request(context, hold_id=hold_id),
            patient_resolution_id=resolution_id,
        ),
    )

    assert result.patient_id == patient.id


def test_booking_succeeds_with_created_demo_patient_token() -> None:
    created_patient = Patient(
        id=uuid4(),
        full_name="Felipe Logan",
        date_of_birth=date(1995, 11, 2),
        phone_number=None,
        email="felipe.logan@example.test",
    )
    context, _, patients = _booking_context_with_resolution(patients=[created_patient])
    _, resolution_repository = _patient_identity_resolution_service(patients)
    context.service.patient_identity_resolution = PatientIdentityResolutionService(
        patients=FakePatientRepository(patients),
        resolutions=resolution_repository,
        patient_intake=PatientIntakeService(
            patients=FakePatientRepository(patients),
            mode=VoicePatientIntakeMode.DEMO_AUTO_CREATE,
        ),
    )
    resolution_id = _store_resolution(
        resolution_repository=resolution_repository,
        patient=created_patient,
        match_status=PatientResolutionMatchStatus.CREATED,
        conversation_id=context.conversation.id,
        confirmed=True,
    )
    hold_id = _active_hold_id(context)

    result = context.service.confirm_and_book(
        replace(
            _build_request(
                context,
                hold_id=hold_id,
                patient_name="Felipe Logan",
                patient_email="felipe.logan@example.test",
                patient_date_of_birth=date(1995, 11, 2),
            ),
            patient_resolution_id=resolution_id,
        ),
    )

    assert result.patient_id == created_patient.id


def test_booking_fails_with_unconfirmed_possible_match_token() -> None:
    context, _, patients = _booking_context_with_resolution()
    patient = patients[0]
    _, resolution_repository = _patient_identity_resolution_service(patients)
    context.service.patient_identity_resolution = PatientIdentityResolutionService(
        patients=FakePatientRepository(patients),
        resolutions=resolution_repository,
        patient_intake=PatientIntakeService(
            patients=FakePatientRepository(patients),
            mode=VoicePatientIntakeMode.LOOKUP_ONLY,
        ),
    )
    resolution_id = _store_resolution(
        resolution_repository=resolution_repository,
        patient=patient,
        match_status=PatientResolutionMatchStatus.POSSIBLE_MATCH,
        conversation_id=context.conversation.id,
        confirmed=False,
    )
    hold_id = _active_hold_id(context)

    with pytest.raises(VoiceBookingIdentityConfirmationRequiredError):
        context.service.confirm_and_book(
            replace(
                _build_request(context, hold_id=hold_id),
                patient_resolution_id=resolution_id,
            ),
        )


def test_booking_fails_with_token_from_another_provider_call_id() -> None:
    context, _, patients = _booking_context_with_resolution()
    patient = patients[0]
    _, resolution_repository = _patient_identity_resolution_service(patients)
    context.service.patient_identity_resolution = PatientIdentityResolutionService(
        patients=FakePatientRepository(patients),
        resolutions=resolution_repository,
        patient_intake=PatientIntakeService(
            patients=FakePatientRepository(patients),
            mode=VoicePatientIntakeMode.LOOKUP_ONLY,
        ),
    )
    resolution_id = _store_resolution(
        resolution_repository=resolution_repository,
        patient=patient,
        match_status=PatientResolutionMatchStatus.EXACT_MATCH,
        provider_call_id="other-call",
        conversation_id=context.conversation.id,
        confirmed=True,
    )
    hold_id = _active_hold_id(context)

    with pytest.raises(VoiceBookingIdentityNotResolvedError):
        context.service.confirm_and_book(
            replace(
                _build_request(context, hold_id=hold_id),
                patient_resolution_id=resolution_id,
            ),
        )


def test_booking_fails_with_unknown_resolution_token() -> None:
    context, _, patients = _booking_context_with_resolution()
    _, resolution_repository = _patient_identity_resolution_service(patients)
    context.service.patient_identity_resolution = PatientIdentityResolutionService(
        patients=FakePatientRepository(patients),
        resolutions=resolution_repository,
        patient_intake=PatientIntakeService(
            patients=FakePatientRepository(patients),
            mode=VoicePatientIntakeMode.LOOKUP_ONLY,
        ),
    )
    hold_id = _active_hold_id(context)

    with pytest.raises(VoiceBookingIdentityNotResolvedError):
        context.service.confirm_and_book(
            replace(
                _build_request(context, hold_id=hold_id),
                patient_resolution_id="00000000-0000-4000-8000-000000000099",
            ),
        )


def test_existing_seeded_patient_fallback_without_resolution_token() -> None:
    context = create_voice_booking_confirmation_context(
        voice_patient_intake_mode=VoicePatientIntakeMode.LOOKUP_ONLY,
    )
    hold_id = _active_hold_id(context)

    result = context.service.confirm_and_book(_build_request(context, hold_id=hold_id))

    assert result.patient_id == context.booking_context.patient.id


def test_explicit_confirmation_still_required_with_resolution_token() -> None:
    context, _, patients = _booking_context_with_resolution()
    patient = patients[0]
    _, resolution_repository = _patient_identity_resolution_service(patients)
    context.service.patient_identity_resolution = PatientIdentityResolutionService(
        patients=FakePatientRepository(patients),
        resolutions=resolution_repository,
        patient_intake=PatientIntakeService(
            patients=FakePatientRepository(patients),
            mode=VoicePatientIntakeMode.LOOKUP_ONLY,
        ),
    )
    resolution_id = _store_resolution(
        resolution_repository=resolution_repository,
        patient=patient,
        match_status=PatientResolutionMatchStatus.EXACT_MATCH,
        conversation_id=context.conversation.id,
        confirmed=True,
    )
    hold_id = _active_hold_id(context)

    with pytest.raises(VoiceBookingMissingConfirmationError):
        context.service.confirm_and_book(
            replace(
                _build_request(context, hold_id=hold_id, explicit_confirmation=False),
                patient_resolution_id=resolution_id,
            ),
        )


def test_hold_still_required_with_resolution_token() -> None:
    context, _, patients = _booking_context_with_resolution()
    patient = patients[0]
    _, resolution_repository = _patient_identity_resolution_service(patients)
    context.service.patient_identity_resolution = PatientIdentityResolutionService(
        patients=FakePatientRepository(patients),
        resolutions=resolution_repository,
        patient_intake=PatientIntakeService(
            patients=FakePatientRepository(patients),
            mode=VoicePatientIntakeMode.LOOKUP_ONLY,
        ),
    )
    resolution_id = _store_resolution(
        resolution_repository=resolution_repository,
        patient=patient,
        match_status=PatientResolutionMatchStatus.EXACT_MATCH,
        conversation_id=context.conversation.id,
        confirmed=True,
    )
    context.conversation.conversation_metadata = {"voice_context": {}}

    with pytest.raises(VoiceBookingMissingHoldError):
        context.service.confirm_and_book(
            replace(
                _build_request(context, hold_id=""),
                patient_resolution_id=resolution_id,
            ),
        )


def test_duplicate_booking_retry_remains_idempotent_with_resolution_token() -> None:
    context, _, patients = _booking_context_with_resolution()
    patient = patients[0]
    _, resolution_repository = _patient_identity_resolution_service(patients)
    context.service.patient_identity_resolution = PatientIdentityResolutionService(
        patients=FakePatientRepository(patients),
        resolutions=resolution_repository,
        patient_intake=PatientIntakeService(
            patients=FakePatientRepository(patients),
            mode=VoicePatientIntakeMode.LOOKUP_ONLY,
        ),
    )
    resolution_id = _store_resolution(
        resolution_repository=resolution_repository,
        patient=patient,
        match_status=PatientResolutionMatchStatus.EXACT_MATCH,
        conversation_id=context.conversation.id,
        confirmed=True,
    )
    hold_id = _active_hold_id(context)
    request = replace(
        _build_request(context, hold_id=hold_id),
        patient_resolution_id=resolution_id,
    )

    first = context.service.confirm_and_book(request)
    second = context.service.confirm_and_book(request)

    assert first.appointment_id == second.appointment_id
    assert second.duplicate is True
    assert len(context.tracking_booking.book_calls) == 1
    assert len(context.email_repository.email_jobs) == 1


def test_booking_recovers_patient_resolution_id_from_voice_context() -> None:
    context, _, patients = _booking_context_with_resolution()
    patient = patients[0]
    _, resolution_repository = _patient_identity_resolution_service(patients)
    context.service.patient_identity_resolution = PatientIdentityResolutionService(
        patients=FakePatientRepository(patients),
        resolutions=resolution_repository,
        patient_intake=PatientIntakeService(
            patients=FakePatientRepository(patients),
            mode=VoicePatientIntakeMode.LOOKUP_ONLY,
        ),
    )
    resolution_id = _store_resolution(
        resolution_repository=resolution_repository,
        patient=patient,
        match_status=PatientResolutionMatchStatus.EXACT_MATCH,
        conversation_id=context.conversation.id,
        confirmed=True,
    )
    hold_id = _active_hold_id(context)
    context.conversation.conversation_metadata = {
        "voice_context": {
            "hold_id": hold_id,
            "availability_slot_id": str(context.booking_context.slot.id),
            "patient_resolution_id": resolution_id,
        },
    }

    result = context.service.confirm_and_book(_build_request(context, hold_id=hold_id))

    assert result.patient_id == patient.id


def test_booking_with_resolution_token_ignores_patient_name_casing() -> None:
    context, _, patients = _booking_context_with_resolution()
    patient = patients[0]
    _, resolution_repository = _patient_identity_resolution_service(patients)
    context.service.patient_identity_resolution = PatientIdentityResolutionService(
        patients=FakePatientRepository(patients),
        resolutions=resolution_repository,
        patient_intake=PatientIntakeService(
            patients=FakePatientRepository(patients),
            mode=VoicePatientIntakeMode.LOOKUP_ONLY,
        ),
    )
    resolution_id = _store_resolution(
        resolution_repository=resolution_repository,
        patient=patient,
        match_status=PatientResolutionMatchStatus.EXACT_MATCH,
        conversation_id=context.conversation.id,
        confirmed=True,
    )
    hold_id = _active_hold_id(context)

    result = context.service.confirm_and_book(
        replace(
            _build_request(
                context,
                hold_id=hold_id,
                patient_name=patient.full_name.lower(),
            ),
            patient_resolution_id=resolution_id,
        ),
    )

    assert result.patient_id == patient.id


def test_booking_prefers_resolution_token_over_mismatched_inline_identity() -> None:
    base_context = create_voice_booking_confirmation_context()
    patient = base_context.booking_context.patient
    other_patient = Patient(
        id=uuid4(),
        full_name="Someone Else",
        date_of_birth=date(1990, 1, 1),
        phone_number=None,
        email="someone.else@example.test",
    )
    context, _, patients = _booking_context_with_resolution(
        patients=[patient, other_patient],
    )
    _, resolution_repository = _patient_identity_resolution_service(patients)
    context.service.patient_identity_resolution = PatientIdentityResolutionService(
        patients=FakePatientRepository(patients),
        resolutions=resolution_repository,
        patient_intake=PatientIntakeService(
            patients=FakePatientRepository(patients),
            mode=VoicePatientIntakeMode.LOOKUP_ONLY,
        ),
    )
    resolution_id = _store_resolution(
        resolution_repository=resolution_repository,
        patient=patient,
        match_status=PatientResolutionMatchStatus.EXACT_MATCH,
        conversation_id=context.conversation.id,
        confirmed=True,
    )
    hold_id = _active_hold_id(context)

    result = context.service.confirm_and_book(
        replace(
            _build_request(
                context,
                hold_id=hold_id,
                patient_name=other_patient.full_name,
                patient_email=other_patient.email,
                patient_date_of_birth=other_patient.date_of_birth,
            ),
            patient_resolution_id=resolution_id,
        ),
    )

    assert result.patient_id == patient.id


def test_legacy_fallback_matches_patient_name_case_insensitively() -> None:
    patient = Patient(
        id=uuid4(),
        full_name="Felipe Godoy",
        date_of_birth=date(1992, 4, 10),
        phone_number=None,
        email="felipe.godoy@example.test",
    )
    context = create_voice_booking_confirmation_context(
        patients=[patient],
        voice_patient_intake_mode=VoicePatientIntakeMode.LOOKUP_ONLY,
    )
    hold_id = _active_hold_id(context)

    result = context.service.confirm_and_book(
        replace(
            _build_request(
                context,
                hold_id=hold_id,
                patient_name="Felipe godoy",
                patient_email=patient.email,
                patient_date_of_birth=patient.date_of_birth,
                patient_phone=None,
            ),
        ),
    )

    assert result.patient_id == patient.id


def test_booking_requires_resolution_token_when_context_token_expired() -> None:
    context, _, _patients = _booking_context_with_resolution()
    hold_id = _active_hold_id(context)
    context.conversation.conversation_metadata = {
        "voice_context": {
            "hold_id": hold_id,
            "availability_slot_id": str(context.booking_context.slot.id),
            "patient_resolution_id": "00000000-0000-4000-8000-000000000099",
        },
    }

    with pytest.raises(VoiceBookingResolutionIdRequiredError):
        context.service.confirm_and_book(_build_request(context, hold_id=hold_id))
