from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime
from enum import StrEnum
from uuid import UUID, uuid4


class PatientResolutionMatchStatus(StrEnum):
    EXACT_MATCH = "exact_match"
    POSSIBLE_MATCH = "possible_match"
    MULTIPLE_MATCHES = "multiple_matches"
    NOT_FOUND = "not_found"
    CREATED = "created"


class PatientResolutionNextStep(StrEnum):
    PROCEED_TO_FINAL_BOOKING_CONFIRMATION = "proceed_to_final_booking_confirmation"
    ASK_POSSIBLE_MATCH_CONFIRMATION = "ask_possible_match_confirmation"
    ASK_EMAIL_OR_PHONE = "ask_email_or_phone"
    RETRY_IDENTITY = "retry_identity"
    DEMO_PATIENT_CREATION_DISABLED = "demo_patient_creation_disabled"
    PATIENT_IDENTITY_NOT_RESOLVED = "patient_identity_not_resolved"


@dataclass(frozen=True, slots=True)
class PatientIdentityResolutionRequest:
    patient_name: str
    patient_date_of_birth: date
    provider_call_id: str | None = None
    conversation_id: UUID | None = None
    patient_email: str | None = None
    patient_phone: str | None = None
    caller_claims_existing_patient: bool = True
    allow_demo_patient_creation: bool = False


@dataclass(frozen=True, slots=True)
class PatientIdentityResolutionResult:
    match_status: PatientResolutionMatchStatus
    requires_confirmation: bool
    next_step: PatientResolutionNextStep
    suggested_response_text: str
    display_name: str | None = None
    candidate_display_name: str | None = None
    confirmation_question: str | None = None
    patient_resolution_id: str | None = None


@dataclass(frozen=True, slots=True)
class PatientResolutionRecord:
    resolution_id: UUID
    patient_id: UUID
    match_status: PatientResolutionMatchStatus
    provider_call_id: str | None
    conversation_id: UUID | None
    confirmed: bool
    created_at: datetime

    @classmethod
    def create(
        cls,
        *,
        patient_id: UUID,
        match_status: PatientResolutionMatchStatus,
        provider_call_id: str | None,
        conversation_id: UUID | None,
        confirmed: bool,
    ) -> PatientResolutionRecord:
        return cls(
            resolution_id=uuid4(),
            patient_id=patient_id,
            match_status=match_status,
            provider_call_id=provider_call_id,
            conversation_id=conversation_id,
            confirmed=confirmed,
            created_at=datetime.now(UTC),
        )

    def is_bookable(self) -> bool:
        if self.match_status is PatientResolutionMatchStatus.POSSIBLE_MATCH:
            return self.confirmed

        return self.match_status in {
            PatientResolutionMatchStatus.EXACT_MATCH,
            PatientResolutionMatchStatus.CREATED,
        }
