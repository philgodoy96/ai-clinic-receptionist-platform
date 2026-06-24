from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from enum import StrEnum


class VoicePatientIntakeMode(StrEnum):
    LOOKUP_ONLY = "lookup_only"
    DEMO_AUTO_CREATE = "demo_auto_create"


@dataclass(frozen=True, slots=True)
class PatientIntakeIdentity:
    full_name: str
    date_of_birth: date
    email: str
    phone_number: str | None = None


class PatientIntakeError(Exception):
    """Base error for controlled voice patient intake."""


class PatientIntakeNotFoundError(PatientIntakeError):
    """Raised when no patient matches and intake mode does not allow creation."""
