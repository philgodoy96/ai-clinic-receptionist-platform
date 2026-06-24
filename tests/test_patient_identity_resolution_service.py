from __future__ import annotations

from collections.abc import Sequence
from datetime import date
from uuid import UUID, uuid4

from app.domain.patient_identity_resolution import (
    PatientIdentityResolutionRequest,
    PatientResolutionMatchStatus,
    PatientResolutionNextStep,
)
from app.domain.voice_patient_intake import VoicePatientIntakeMode
from app.models.scheduling import Patient
from app.repositories.memory.patient_resolution import InMemoryPatientResolutionRepository
from app.services.patient_identity_resolution import PatientIdentityResolutionService
from app.services.patient_intake import PatientIntakeService


class FakePatientRepository:
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


def _service(
    patients: Sequence[Patient],
    *,
    mode: VoicePatientIntakeMode = VoicePatientIntakeMode.LOOKUP_ONLY,
) -> PatientIdentityResolutionService:
    repository = FakePatientRepository(patients)
    return PatientIdentityResolutionService(
        patients=repository,
        resolutions=InMemoryPatientResolutionRepository(),
        patient_intake=PatientIntakeService(
            patients=repository,
            mode=mode,
        ),
    )


def test_exact_match_by_full_name_and_dob() -> None:
    patient = Patient(
        id=uuid4(),
        full_name="John Miller",
        date_of_birth=date(1985, 4, 12),
        phone_number="+1-555-0201",
        email="john.miller@example.test",
    )
    service = _service([patient])

    result = service.resolve(
        PatientIdentityResolutionRequest(
            patient_name="John Miller",
            patient_date_of_birth=date(1985, 4, 12),
            provider_call_id="call-123",
        ),
    )

    assert result.match_status is PatientResolutionMatchStatus.EXACT_MATCH
    assert result.requires_confirmation is False
    assert result.next_step is PatientResolutionNextStep.PROCEED_TO_BOOKING
    assert result.patient_resolution_id is not None
    assert result.patient_resolution_id != str(patient.id)


def test_possible_match_by_shortened_name_and_dob() -> None:
    patient = Patient(
        id=uuid4(),
        full_name="Michael Lee Reed",
        date_of_birth=date(1988, 3, 15),
        phone_number=None,
        email="michael.lee.reed@example.test",
    )
    service = _service([patient])

    result = service.resolve(
        PatientIdentityResolutionRequest(
            patient_name="Michael Reed",
            patient_date_of_birth=date(1988, 3, 15),
            patient_email="michael.lee.reed@example.test",
            provider_call_id="call-456",
        ),
    )

    assert result.match_status is PatientResolutionMatchStatus.POSSIBLE_MATCH
    assert result.requires_confirmation is True
    assert result.next_step is PatientResolutionNextStep.CONFIRM_IDENTITY
    assert result.candidate_display_name == "Michael Lee Reed"
    assert result.confirmation_question is not None
    assert "possible existing profile" in result.confirmation_question.lower()
    assert "Michael Lee Reed" in result.confirmation_question
    assert "michael.lee.reed@example.test" not in (result.confirmation_question or "").lower()
    assert result.patient_resolution_id is not None


def test_new_caller_with_exact_existing_patient_returns_exact_match_without_create() -> None:
    patient = Patient(
        id=uuid4(),
        full_name="John Miller",
        date_of_birth=date(1985, 4, 12),
        phone_number="+1-555-0201",
        email="john.miller@example.test",
    )
    repository = FakePatientRepository([patient])
    service = PatientIdentityResolutionService(
        patients=repository,
        resolutions=InMemoryPatientResolutionRepository(),
        patient_intake=PatientIntakeService(
            patients=repository,
            mode=VoicePatientIntakeMode.DEMO_AUTO_CREATE,
        ),
    )

    result = service.resolve(
        PatientIdentityResolutionRequest(
            patient_name="John Miller",
            patient_date_of_birth=date(1985, 4, 12),
            patient_email="john.miller.new@example.test",
            caller_claims_existing_patient=False,
            allow_demo_patient_creation=True,
            provider_call_id="call-new-exact",
        ),
    )

    assert result.match_status is PatientResolutionMatchStatus.EXACT_MATCH
    assert result.patient_resolution_id is not None
    assert len(repository.patients) == 1


def test_new_caller_with_possible_existing_patient_does_not_create_duplicate() -> None:
    patient = Patient(
        id=uuid4(),
        full_name="Michael Lee Reed",
        date_of_birth=date(1985, 4, 12),
        phone_number=None,
        email="michael.lee.reed@example.test",
    )
    repository = FakePatientRepository([patient])
    service = PatientIdentityResolutionService(
        patients=repository,
        resolutions=InMemoryPatientResolutionRepository(),
        patient_intake=PatientIntakeService(
            patients=repository,
            mode=VoicePatientIntakeMode.DEMO_AUTO_CREATE,
        ),
    )

    result = service.resolve(
        PatientIdentityResolutionRequest(
            patient_name="Michael Reed",
            patient_date_of_birth=date(1985, 4, 12),
            patient_email="michael.reed@example.test",
            caller_claims_existing_patient=False,
            allow_demo_patient_creation=True,
            provider_call_id="call-new-possible",
        ),
    )

    assert result.match_status is PatientResolutionMatchStatus.POSSIBLE_MATCH
    assert result.requires_confirmation is True
    assert result.confirmation_question is not None
    assert "Michael Lee Reed" in result.confirmation_question
    assert "michael.lee.reed@example.test" not in result.confirmation_question.lower()
    assert "michael.reed@example.test" not in (result.suggested_response_text or "").lower()
    assert len(repository.patients) == 1


def test_rejected_possible_match_suggests_email_or_new_demo_path() -> None:
    patient = Patient(
        id=uuid4(),
        full_name="Michael Lee Reed",
        date_of_birth=date(1985, 4, 12),
        phone_number=None,
        email="michael.lee.reed@example.test",
    )
    service = _service([patient], mode=VoicePatientIntakeMode.DEMO_AUTO_CREATE)

    resolved = service.resolve(
        PatientIdentityResolutionRequest(
            patient_name="Michael Reed",
            patient_date_of_birth=date(1985, 4, 12),
            patient_email="michael.reed@example.test",
            caller_claims_existing_patient=False,
            allow_demo_patient_creation=True,
            provider_call_id="call-reject",
        ),
    )
    assert resolved.patient_resolution_id is not None

    rejected = service.reject_resolution(
        patient_resolution_id=resolved.patient_resolution_id,
        provider_call_id="call-reject",
    )

    assert rejected.match_status is PatientResolutionMatchStatus.NOT_FOUND
    assert rejected.next_step is PatientResolutionNextStep.RETRY_IDENTITY
    lowered = rejected.suggested_response_text.lower()
    assert "email" in lowered or "phone" in lowered
    assert "first time" in lowered or "sample contact" in lowered


def test_possible_match_response_does_not_expose_stored_contact_details() -> None:
    patient = Patient(
        id=uuid4(),
        full_name="Michael Lee Reed",
        date_of_birth=date(1988, 3, 15),
        phone_number="+1-555-0199",
        email="michael.lee.reed@example.test",
    )
    service = _service([patient])

    result = service.resolve(
        PatientIdentityResolutionRequest(
            patient_name="Michael Reed",
            patient_date_of_birth=date(1988, 3, 15),
            provider_call_id="call-privacy",
        ),
    )

    combined = " ".join(
        filter(
            None,
            [
                result.confirmation_question,
                result.suggested_response_text,
                result.candidate_display_name,
            ],
        ),
    ).lower()
    assert "michael.lee.reed@example.test" not in combined
    assert "+1-555-0199" not in combined
    assert "555-0199" not in combined


def test_multiple_matches_when_name_and_dob_collide() -> None:
    shared_dob = date(1990, 7, 22)
    patients = [
        Patient(
            id=uuid4(),
            full_name="Sarah Chen",
            date_of_birth=shared_dob,
            phone_number="+1-555-0301",
            email="sarah.chen@example.test",
        ),
        Patient(
            id=uuid4(),
            full_name="Sarah Chen",
            date_of_birth=shared_dob,
            phone_number="+1-555-0302",
            email="sarah.chen.work@example.test",
        ),
    ]
    service = _service(patients)

    result = service.resolve(
        PatientIdentityResolutionRequest(
            patient_name="Sarah Chen",
            patient_date_of_birth=shared_dob,
            provider_call_id="call-789",
        ),
    )

    assert result.match_status is PatientResolutionMatchStatus.MULTIPLE_MATCHES
    assert result.requires_confirmation is True
    assert result.next_step is PatientResolutionNextStep.COLLECT_EMAIL
    assert result.patient_resolution_id is None
    assert "email" in result.suggested_response_text.lower()
    assert "sarah.chen@example.test" not in result.suggested_response_text


def test_not_found_for_existing_patient_claim_without_match() -> None:
    service = _service([])

    result = service.resolve(
        PatientIdentityResolutionRequest(
            patient_name="John Miller",
            patient_date_of_birth=date(1985, 4, 12),
            patient_email="john.miller@example.test",
            caller_claims_existing_patient=True,
            allow_demo_patient_creation=True,
            provider_call_id="call-000",
        ),
    )

    assert result.match_status is PatientResolutionMatchStatus.NOT_FOUND
    assert result.patient_resolution_id is None
    assert result.next_step is PatientResolutionNextStep.RETRY_IDENTITY


def test_new_demo_patient_creation() -> None:
    repository = FakePatientRepository([])
    service = PatientIdentityResolutionService(
        patients=repository,
        resolutions=InMemoryPatientResolutionRepository(),
        patient_intake=PatientIntakeService(
            patients=repository,
            mode=VoicePatientIntakeMode.DEMO_AUTO_CREATE,
        ),
    )

    result = service.resolve(
        PatientIdentityResolutionRequest(
            patient_name="Felipe Logan",
            patient_date_of_birth=date(1995, 11, 2),
            patient_email="felipe.logan@example.test",
            caller_claims_existing_patient=False,
            allow_demo_patient_creation=True,
            provider_call_id="call-create",
        ),
    )

    assert result.match_status is PatientResolutionMatchStatus.CREATED
    assert result.patient_resolution_id is not None
    assert len(repository.patients) == 1
    assert repository.patients[0].phone_number is None


def test_no_demo_creation_in_lookup_only_mode() -> None:
    service = _service([], mode=VoicePatientIntakeMode.LOOKUP_ONLY)

    result = service.resolve(
        PatientIdentityResolutionRequest(
            patient_name="Felipe Logan",
            patient_date_of_birth=date(1995, 11, 2),
            patient_email="felipe.logan@example.test",
            caller_claims_existing_patient=False,
            allow_demo_patient_creation=False,
            provider_call_id="call-lookup-only",
        ),
    )

    assert result.match_status is PatientResolutionMatchStatus.NOT_FOUND
    assert result.patient_resolution_id is None


def test_no_phone_generated_when_absent() -> None:
    repository = FakePatientRepository([])
    service = PatientIdentityResolutionService(
        patients=repository,
        resolutions=InMemoryPatientResolutionRepository(),
        patient_intake=PatientIntakeService(
            patients=repository,
            mode=VoicePatientIntakeMode.DEMO_AUTO_CREATE,
        ),
    )

    service.resolve(
        PatientIdentityResolutionRequest(
            patient_name="Ava Thompson",
            patient_date_of_birth=date(1992, 9, 3),
            patient_email="ava.thompson@example.test",
            caller_claims_existing_patient=False,
            allow_demo_patient_creation=True,
            provider_call_id="call-no-phone",
        ),
    )

    assert len(repository.patients) == 1
    assert repository.patients[0].phone_number is None


def test_resolution_token_is_opaque_and_scoped_to_provider_call() -> None:
    patient = Patient(
        id=uuid4(),
        full_name="John Miller",
        date_of_birth=date(1985, 4, 12),
        phone_number="+1-555-0201",
        email="john.miller@example.test",
    )
    service = _service([patient])

    result = service.resolve(
        PatientIdentityResolutionRequest(
            patient_name="John Miller",
            patient_date_of_birth=date(1985, 4, 12),
            provider_call_id="call-scope-a",
        ),
    )

    assert result.patient_resolution_id is not None
    assert result.patient_resolution_id != str(patient.id)

    scoped = service.get_resolution_for_booking(
        patient_resolution_id=result.patient_resolution_id,
        provider_call_id="call-scope-a",
    )
    assert scoped is not None
    assert scoped.patient_id == patient.id

    wrong_call = service.get_resolution_for_booking(
        patient_resolution_id=result.patient_resolution_id,
        provider_call_id="call-scope-b",
    )
    assert wrong_call is None


def test_existing_patient_lookup_by_email() -> None:
    patient = Patient(
        id=uuid4(),
        full_name="John Miller",
        date_of_birth=date(1985, 4, 12),
        phone_number="+1-555-0201",
        email="john.miller@example.test",
    )
    service = _service([patient])

    result = service.resolve(
        PatientIdentityResolutionRequest(
            patient_name="John Miller",
            patient_date_of_birth=date(1985, 4, 12),
            patient_email="JOHN.MILLER@EXAMPLE.TEST",
            provider_call_id="call-email",
        ),
    )

    assert result.match_status is PatientResolutionMatchStatus.EXACT_MATCH
    assert result.patient_resolution_id is not None


def test_possible_match_requires_confirmation_before_booking() -> None:
    patient = Patient(
        id=uuid4(),
        full_name="Michael Lee Reed",
        date_of_birth=date(1988, 3, 15),
        phone_number=None,
        email="michael.lee.reed@example.test",
    )
    service = _service([patient])

    result = service.resolve(
        PatientIdentityResolutionRequest(
            patient_name="Michael Reed",
            patient_date_of_birth=date(1988, 3, 15),
            patient_email="michael.lee.reed@example.test",
            provider_call_id="call-confirm",
        ),
    )

    assert result.patient_resolution_id is not None
    unconfirmed = service.get_resolution_for_booking(
        patient_resolution_id=result.patient_resolution_id,
        provider_call_id="call-confirm",
    )
    assert unconfirmed is None

    confirmed = service.confirm_resolution(
        patient_resolution_id=result.patient_resolution_id,
        provider_call_id="call-confirm",
    )
    assert confirmed.requires_confirmation is False

    bookable = service.get_resolution_for_booking(
        patient_resolution_id=result.patient_resolution_id,
        provider_call_id="call-confirm",
    )
    assert bookable is not None
    assert bookable.confirmed is True


def test_demo_creation_is_idempotent_under_retries() -> None:
    repository = FakePatientRepository([])
    service = PatientIdentityResolutionService(
        patients=repository,
        resolutions=InMemoryPatientResolutionRepository(),
        patient_intake=PatientIntakeService(
            patients=repository,
            mode=VoicePatientIntakeMode.DEMO_AUTO_CREATE,
        ),
    )
    request = PatientIdentityResolutionRequest(
        patient_name="Ava Thompson",
        patient_date_of_birth=date(1992, 9, 3),
        patient_email="ava.thompson@example.test",
        caller_claims_existing_patient=False,
        allow_demo_patient_creation=True,
        provider_call_id="call-idempotent",
    )

    first = service.resolve(request)
    second = service.resolve(request)

    assert first.match_status is PatientResolutionMatchStatus.CREATED
    assert second.match_status is PatientResolutionMatchStatus.EXACT_MATCH
    assert len(repository.patients) == 1
