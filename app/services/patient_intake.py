from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date
from uuid import uuid4

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.domain.patient_identity_matching import is_demo_sample_email
from app.domain.voice_patient_intake import (
    PatientIntakeIdentity,
    PatientIntakeNotFoundError,
    VoicePatientIntakeMode,
)
from app.models.scheduling import Patient
from app.repositories.scheduling import PatientRepository
from app.services.scheduling import InsufficientPatientIdentityError

_WHITESPACE_PATTERN = re.compile(r"\s+")


@dataclass(frozen=True, slots=True)
class NormalizedPatientIntakeIdentity:
    full_name: str
    date_of_birth: date
    email: str
    phone_number: str | None


class PatientIntakeService:
    def __init__(
        self,
        *,
        patients: PatientRepository,
        mode: VoicePatientIntakeMode = VoicePatientIntakeMode.LOOKUP_ONLY,
        db: Session | None = None,
    ) -> None:
        self.patients = patients
        self.mode = mode
        self.db = db

    def resolve_for_voice_booking(self, identity: PatientIntakeIdentity) -> Patient:
        normalized = self._normalize_identity(identity)
        self._validate_identity_for_lookup(normalized)

        patient = self._lookup_existing_patient(normalized)
        if patient is not None:
            return patient

        if self.mode is VoicePatientIntakeMode.LOOKUP_ONLY:
            msg = "patient was not found"
            raise PatientIntakeNotFoundError(msg)

        if not is_demo_sample_email(normalized.email):
            msg = "patient was not found"
            raise PatientIntakeNotFoundError(msg)

        return self._create_demo_patient_idempotent(normalized)

    def _normalize_identity(
        self,
        identity: PatientIntakeIdentity,
    ) -> NormalizedPatientIntakeIdentity:
        full_name = _WHITESPACE_PATTERN.sub(" ", identity.full_name.strip())
        email = identity.email.strip().lower()
        phone_number = identity.phone_number.strip() if identity.phone_number else None
        if phone_number == "":
            phone_number = None

        return NormalizedPatientIntakeIdentity(
            full_name=full_name,
            date_of_birth=identity.date_of_birth,
            email=email,
            phone_number=phone_number,
        )

    def _validate_identity_for_lookup(self, identity: NormalizedPatientIntakeIdentity) -> None:
        if not identity.full_name:
            msg = "patient identity is incomplete"
            raise InsufficientPatientIdentityError(msg)

        if not identity.email:
            msg = "patient identity is incomplete"
            raise InsufficientPatientIdentityError(msg)

    def _lookup_existing_patient(
        self,
        identity: NormalizedPatientIntakeIdentity,
    ) -> Patient | None:
        return self.patients.get_by_identity(
            full_name=identity.full_name,
            date_of_birth=identity.date_of_birth,
            phone_number=identity.phone_number,
            email=identity.email,
        )

    def _create_demo_patient_idempotent(
        self,
        identity: NormalizedPatientIntakeIdentity,
    ) -> Patient:
        existing_by_email = self.patients.get_by_email(identity.email)
        if existing_by_email is not None:
            if self._identity_matches_patient(identity, existing_by_email):
                return existing_by_email
            msg = "patient was not found"
            raise PatientIntakeNotFoundError(msg)

        if identity.phone_number is not None:
            existing_by_phone = self.patients.get_by_phone_number(identity.phone_number)
            if existing_by_phone is not None:
                if self._identity_matches_patient(identity, existing_by_phone):
                    return existing_by_phone
                msg = "patient was not found"
                raise PatientIntakeNotFoundError(msg)

        patient = Patient(
            id=uuid4(),
            full_name=identity.full_name,
            date_of_birth=identity.date_of_birth,
            email=identity.email,
            phone_number=identity.phone_number,
        )

        try:
            return self.patients.add(patient)
        except IntegrityError as exc:
            if self.db is not None:
                self.db.rollback()

            recovered = self._lookup_existing_patient(identity)
            if recovered is not None:
                return recovered

            recovered_by_email = self.patients.get_by_email(identity.email)
            if recovered_by_email is not None and self._identity_matches_patient(
                identity,
                recovered_by_email,
            ):
                return recovered_by_email

            msg = "patient was not found"
            raise PatientIntakeNotFoundError(msg) from exc

    @staticmethod
    def _identity_matches_patient(
        identity: NormalizedPatientIntakeIdentity,
        patient: Patient,
    ) -> bool:
        if patient.full_name != identity.full_name:
            return False

        if patient.date_of_birth != identity.date_of_birth:
            return False

        if patient.email.lower() != identity.email:
            return False

        if identity.phone_number is None:
            return True

        return patient.phone_number == identity.phone_number
