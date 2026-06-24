from __future__ import annotations

from typing import Protocol
from uuid import UUID

from app.domain.patient_identity_resolution import PatientResolutionRecord


class PatientResolutionRepository(Protocol):
    def save(self, record: PatientResolutionRecord, *, ttl_seconds: int) -> None:
        raise NotImplementedError

    def get(self, resolution_id: UUID) -> PatientResolutionRecord | None:
        raise NotImplementedError

    def update(self, record: PatientResolutionRecord, *, ttl_seconds: int) -> None:
        raise NotImplementedError
