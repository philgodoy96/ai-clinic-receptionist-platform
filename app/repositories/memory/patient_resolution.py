from __future__ import annotations

from uuid import UUID

from app.domain.patient_identity_resolution import PatientResolutionRecord


class InMemoryPatientResolutionRepository:
    def __init__(self) -> None:
        self._records: dict[UUID, PatientResolutionRecord] = {}

    def save(self, record: PatientResolutionRecord, *, ttl_seconds: int) -> None:
        del ttl_seconds
        self._records[record.resolution_id] = record

    def get(self, resolution_id: UUID) -> PatientResolutionRecord | None:
        return self._records.get(resolution_id)

    def update(self, record: PatientResolutionRecord, *, ttl_seconds: int) -> None:
        self.save(record, ttl_seconds=ttl_seconds)

    def delete(self, resolution_id: UUID) -> None:
        self._records.pop(resolution_id, None)
