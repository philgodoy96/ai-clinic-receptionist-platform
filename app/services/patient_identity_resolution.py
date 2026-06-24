from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from uuid import UUID

from app.domain.patient_identity_matching import (
    build_confirmation_question,
    classify_name_match,
    normalize_email,
    normalize_optional_phone,
    normalize_patient_name,
)
from app.domain.patient_identity_resolution import (
    PatientIdentityResolutionRequest,
    PatientIdentityResolutionResult,
    PatientResolutionMatchStatus,
    PatientResolutionNextStep,
    PatientResolutionRecord,
)
from app.domain.scheduling.phone import normalize_phone_digits
from app.domain.voice_patient_intake import (
    PatientIntakeIdentity,
    PatientIntakeNotFoundError,
    VoicePatientIntakeMode,
)
from app.models.scheduling import Patient
from app.repositories.patient_resolution import PatientResolutionRepository
from app.repositories.scheduling import PatientRepository
from app.services.patient_intake import PatientIntakeService
from app.services.scheduling import InsufficientPatientIdentityError


class PatientIdentityResolutionError(Exception):
    """Base error for patient identity resolution."""


class PatientIdentityIncompleteError(PatientIdentityResolutionError):
    """Raised when required identity fields are missing."""


@dataclass(frozen=True, slots=True)
class NormalizedResolutionIdentity:
    patient_name: str
    patient_date_of_birth: date
    patient_email: str | None
    patient_phone: str | None


class PatientIdentityResolutionService:
    def __init__(
        self,
        *,
        patients: PatientRepository,
        resolutions: PatientResolutionRepository,
        patient_intake: PatientIntakeService | None = None,
        resolution_ttl_seconds: int = 300,
    ) -> None:
        self.patients = patients
        self.resolutions = resolutions
        self.patient_intake = patient_intake
        self.resolution_ttl_seconds = resolution_ttl_seconds

    def resolve(self, request: PatientIdentityResolutionRequest) -> PatientIdentityResolutionResult:
        identity = self._normalize_request(request)
        self._validate_identity(identity)

        matched = self._match_patients(identity)
        if matched.status is PatientResolutionMatchStatus.MULTIPLE_MATCHES:
            return self._build_multiple_matches_result(identity)

        if matched.status is PatientResolutionMatchStatus.POSSIBLE_MATCH:
            if matched.patient is None:
                return self._build_not_found_result(identity)
            return self._build_possible_match_result(
                identity=identity,
                patient=matched.patient,
                request=request,
            )

        if matched.status is PatientResolutionMatchStatus.EXACT_MATCH:
            if matched.patient is None:
                return self._build_not_found_result(identity)
            return self._build_exact_match_result(
                identity=identity,
                patient=matched.patient,
                request=request,
            )

        if request.caller_claims_existing_patient or not request.allow_demo_patient_creation:
            return self._build_not_found_result(identity)

        return self._create_demo_patient(identity=identity, request=request)

    def confirm_resolution(
        self,
        *,
        patient_resolution_id: str,
        provider_call_id: str | None = None,
        conversation_id: UUID | None = None,
    ) -> PatientIdentityResolutionResult:
        record = self._load_scoped_record(
            patient_resolution_id=patient_resolution_id,
            provider_call_id=provider_call_id,
            conversation_id=conversation_id,
        )
        if record is None:
            return self._build_not_found_result(
                NormalizedResolutionIdentity(
                    patient_name="",
                    patient_date_of_birth=date.min,
                    patient_email=None,
                    patient_phone=None,
                ),
            )

        if record.match_status is not PatientResolutionMatchStatus.POSSIBLE_MATCH:
            patient = self.patients.get_by_id(record.patient_id)
            if patient is None:
                return self._build_not_found_result(
                    NormalizedResolutionIdentity(
                        patient_name="",
                        patient_date_of_birth=date.min,
                        patient_email=None,
                        patient_phone=None,
                    ),
                )

            return self._build_exact_match_result(
                identity=NormalizedResolutionIdentity(
                    patient_name=patient.full_name,
                    patient_date_of_birth=patient.date_of_birth,
                    patient_email=None,
                    patient_phone=None,
                ),
                patient=patient,
                request=PatientIdentityResolutionRequest(
                    patient_name=patient.full_name,
                    patient_date_of_birth=patient.date_of_birth,
                    provider_call_id=provider_call_id,
                    conversation_id=conversation_id,
                ),
            )

        confirmed_record = PatientResolutionRecord(
            resolution_id=record.resolution_id,
            patient_id=record.patient_id,
            match_status=record.match_status,
            provider_call_id=record.provider_call_id,
            conversation_id=record.conversation_id,
            confirmed=True,
            created_at=record.created_at,
        )
        self.resolutions.update(confirmed_record, ttl_seconds=self.resolution_ttl_seconds)

        patient = self.patients.get_by_id(record.patient_id)
        if patient is None:
            return self._build_not_found_result(
                NormalizedResolutionIdentity(
                    patient_name="",
                    patient_date_of_birth=date.min,
                    patient_email=None,
                    patient_phone=None,
                ),
            )

        return PatientIdentityResolutionResult(
            match_status=PatientResolutionMatchStatus.POSSIBLE_MATCH,
            requires_confirmation=False,
            display_name=patient.full_name,
            candidate_display_name=patient.full_name,
            confirmation_question=None,
            patient_resolution_id=str(confirmed_record.resolution_id),
            next_step=PatientResolutionNextStep.PROCEED_TO_BOOKING,
            suggested_response_text="Thanks for confirming. Let's finish booking your appointment.",
        )

    def get_resolution_for_booking(
        self,
        *,
        patient_resolution_id: str,
        provider_call_id: str | None = None,
        conversation_id: UUID | None = None,
    ) -> PatientResolutionRecord | None:
        record = self._load_scoped_record(
            patient_resolution_id=patient_resolution_id,
            provider_call_id=provider_call_id,
            conversation_id=conversation_id,
        )
        if record is None or not record.is_bookable():
            return None

        return record

    def _normalize_request(
        self,
        request: PatientIdentityResolutionRequest,
    ) -> NormalizedResolutionIdentity:
        email = normalize_email(request.patient_email) if request.patient_email else None
        if email == "":
            email = None

        return NormalizedResolutionIdentity(
            patient_name=normalize_patient_name(request.patient_name),
            patient_date_of_birth=request.patient_date_of_birth,
            patient_email=email,
            patient_phone=normalize_optional_phone(request.patient_phone),
        )

    def _validate_identity(self, identity: NormalizedResolutionIdentity) -> None:
        if not identity.patient_name:
            msg = "patient identity is incomplete"
            raise PatientIdentityIncompleteError(msg)

    def _match_patients(self, identity: NormalizedResolutionIdentity) -> _MatchOutcome:
        if identity.patient_email is not None:
            email_match = self._match_by_email(identity)
            if email_match is not None:
                return email_match

        if identity.patient_phone is not None:
            phone_match = self._match_by_phone(identity)
            if phone_match is not None:
                return phone_match

        return self._match_by_name_and_dob(identity)

    def _match_by_email(self, identity: NormalizedResolutionIdentity) -> _MatchOutcome | None:
        patient = self.patients.get_by_email(identity.patient_email or "")
        if patient is None:
            return None

        if patient.date_of_birth != identity.patient_date_of_birth:
            return None

        name_match = classify_name_match(identity.patient_name, patient.full_name)
        if name_match == "exact":
            return _MatchOutcome(PatientResolutionMatchStatus.EXACT_MATCH, patient)

        if name_match == "possible":
            return _MatchOutcome(PatientResolutionMatchStatus.POSSIBLE_MATCH, patient)

        return None

    def _match_by_phone(self, identity: NormalizedResolutionIdentity) -> _MatchOutcome | None:
        patient = self.patients.get_by_phone_number(identity.patient_phone or "")
        if patient is None:
            normalized_phone = normalize_phone_digits(identity.patient_phone or "")
            for candidate in self.patients.list_by_date_of_birth(identity.patient_date_of_birth):
                if candidate.phone_number is None:
                    continue
                if normalize_phone_digits(candidate.phone_number) == normalized_phone:
                    patient = candidate
                    break

        if patient is None:
            return None

        if patient.date_of_birth != identity.patient_date_of_birth:
            return None

        name_match = classify_name_match(identity.patient_name, patient.full_name)
        if name_match == "exact":
            return _MatchOutcome(PatientResolutionMatchStatus.EXACT_MATCH, patient)

        if name_match == "possible":
            return _MatchOutcome(PatientResolutionMatchStatus.POSSIBLE_MATCH, patient)

        return None

    def _match_by_name_and_dob(self, identity: NormalizedResolutionIdentity) -> _MatchOutcome:
        candidates = self.patients.list_by_date_of_birth(identity.patient_date_of_birth)
        exact_matches: list[Patient] = []
        possible_matches: list[Patient] = []

        for candidate in candidates:
            name_match = classify_name_match(identity.patient_name, candidate.full_name)
            if name_match == "exact":
                exact_matches.append(candidate)
            elif name_match == "possible":
                possible_matches.append(candidate)

        if len(exact_matches) > 1:
            return _MatchOutcome(PatientResolutionMatchStatus.MULTIPLE_MATCHES, None)

        if len(exact_matches) == 1:
            return _MatchOutcome(PatientResolutionMatchStatus.EXACT_MATCH, exact_matches[0])

        if len(possible_matches) > 1:
            return _MatchOutcome(PatientResolutionMatchStatus.MULTIPLE_MATCHES, None)

        if len(possible_matches) == 1:
            return _MatchOutcome(PatientResolutionMatchStatus.POSSIBLE_MATCH, possible_matches[0])

        return _MatchOutcome(PatientResolutionMatchStatus.NOT_FOUND, None)

    def _create_demo_patient(
        self,
        *,
        identity: NormalizedResolutionIdentity,
        request: PatientIdentityResolutionRequest,
    ) -> PatientIdentityResolutionResult:
        if identity.patient_email is None:
            return self._build_not_found_result(identity)

        intake = self.patient_intake or PatientIntakeService(
            patients=self.patients,
            mode=VoicePatientIntakeMode.DEMO_AUTO_CREATE,
        )

        try:
            patient = intake.resolve_for_voice_booking(
                PatientIntakeIdentity(
                    full_name=identity.patient_name,
                    date_of_birth=identity.patient_date_of_birth,
                    email=identity.patient_email,
                    phone_number=identity.patient_phone,
                ),
            )
        except (InsufficientPatientIdentityError, PatientIntakeNotFoundError):
            return self._build_not_found_result(identity)

        record = self._persist_resolution(
            patient=patient,
            match_status=PatientResolutionMatchStatus.CREATED,
            request=request,
            confirmed=True,
        )

        return PatientIdentityResolutionResult(
            match_status=PatientResolutionMatchStatus.CREATED,
            requires_confirmation=False,
            display_name=identity.patient_name,
            candidate_display_name=patient.full_name,
            confirmation_question=None,
            patient_resolution_id=str(record.resolution_id),
            next_step=PatientResolutionNextStep.PROCEED_TO_BOOKING,
            suggested_response_text=(
                "I've added your details for this visit. Let's finish booking your appointment."
            ),
        )

    def _build_exact_match_result(
        self,
        *,
        identity: NormalizedResolutionIdentity,
        patient: Patient,
        request: PatientIdentityResolutionRequest,
    ) -> PatientIdentityResolutionResult:
        record = self._persist_resolution(
            patient=patient,
            match_status=PatientResolutionMatchStatus.EXACT_MATCH,
            request=request,
            confirmed=True,
        )

        return PatientIdentityResolutionResult(
            match_status=PatientResolutionMatchStatus.EXACT_MATCH,
            requires_confirmation=False,
            display_name=identity.patient_name,
            candidate_display_name=patient.full_name,
            confirmation_question=None,
            patient_resolution_id=str(record.resolution_id),
            next_step=PatientResolutionNextStep.PROCEED_TO_BOOKING,
            suggested_response_text=(
                "I found your chart. Let's finish booking your appointment."
            ),
        )

    def _build_possible_match_result(
        self,
        *,
        identity: NormalizedResolutionIdentity,
        patient: Patient,
        request: PatientIdentityResolutionRequest,
    ) -> PatientIdentityResolutionResult:
        record = self._persist_resolution(
            patient=patient,
            match_status=PatientResolutionMatchStatus.POSSIBLE_MATCH,
            request=request,
            confirmed=False,
        )
        confirmation_question = build_confirmation_question(patient)

        return PatientIdentityResolutionResult(
            match_status=PatientResolutionMatchStatus.POSSIBLE_MATCH,
            requires_confirmation=True,
            display_name=identity.patient_name,
            candidate_display_name=patient.full_name,
            confirmation_question=confirmation_question,
            patient_resolution_id=str(record.resolution_id),
            next_step=PatientResolutionNextStep.CONFIRM_IDENTITY,
            suggested_response_text=confirmation_question,
        )

    def _build_multiple_matches_result(
        self,
        identity: NormalizedResolutionIdentity,
    ) -> PatientIdentityResolutionResult:
        if identity.patient_email is None:
            return PatientIdentityResolutionResult(
                match_status=PatientResolutionMatchStatus.MULTIPLE_MATCHES,
                requires_confirmation=True,
                display_name=identity.patient_name,
                candidate_display_name=None,
                confirmation_question=None,
                patient_resolution_id=None,
                next_step=PatientResolutionNextStep.COLLECT_EMAIL,
                suggested_response_text=(
                    "I need a bit more information to find your chart. "
                    "Which email address do you have on file with us?"
                ),
            )

        return PatientIdentityResolutionResult(
            match_status=PatientResolutionMatchStatus.MULTIPLE_MATCHES,
            requires_confirmation=True,
            display_name=identity.patient_name,
            candidate_display_name=None,
            confirmation_question=None,
            patient_resolution_id=None,
            next_step=PatientResolutionNextStep.COLLECT_PHONE,
            suggested_response_text=(
                "I need one more detail to find your chart. "
                "What phone number do you have on file with us?"
            ),
        )

    def _build_not_found_result(
        self,
        identity: NormalizedResolutionIdentity,
    ) -> PatientIdentityResolutionResult:
        return PatientIdentityResolutionResult(
            match_status=PatientResolutionMatchStatus.NOT_FOUND,
            requires_confirmation=False,
            display_name=identity.patient_name or None,
            candidate_display_name=None,
            confirmation_question=None,
            patient_resolution_id=None,
            next_step=PatientResolutionNextStep.RETRY_IDENTITY,
            suggested_response_text=(
                "I'm not matching those details yet. "
                "Could we try your name and date of birth once more?"
            ),
        )

    def _persist_resolution(
        self,
        *,
        patient: Patient,
        match_status: PatientResolutionMatchStatus,
        request: PatientIdentityResolutionRequest,
        confirmed: bool,
    ) -> PatientResolutionRecord:
        record = PatientResolutionRecord.create(
            patient_id=patient.id,
            match_status=match_status,
            provider_call_id=request.provider_call_id,
            conversation_id=request.conversation_id,
            confirmed=confirmed,
        )
        self.resolutions.save(record, ttl_seconds=self.resolution_ttl_seconds)
        return record

    def _load_scoped_record(
        self,
        *,
        patient_resolution_id: str,
        provider_call_id: str | None,
        conversation_id: UUID | None,
    ) -> PatientResolutionRecord | None:
        try:
            resolution_id = UUID(patient_resolution_id)
        except ValueError:
            return None

        record = self.resolutions.get(resolution_id)
        if record is None:
            return None

        if provider_call_id is not None and record.provider_call_id != provider_call_id:
            return None

        if (
            conversation_id is not None
            and record.conversation_id is not None
            and record.conversation_id != conversation_id
        ):
            return None

        return record


@dataclass(frozen=True, slots=True)
class _MatchOutcome:
    status: PatientResolutionMatchStatus
    patient: Patient | None
