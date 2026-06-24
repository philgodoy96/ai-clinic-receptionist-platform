from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from app.domain.patient_identity_resolution import (
    PatientResolutionMatchStatus,
    PatientResolutionRecord,
)


class RedisPatientResolutionRepository:
    def __init__(
        self,
        redis_client: Any,
        *,
        key_prefix: str = "patient_resolution",
    ) -> None:
        self.redis_client = redis_client
        self.key_prefix = key_prefix

    def save(self, record: PatientResolutionRecord, *, ttl_seconds: int) -> None:
        payload = self._serialize(record)
        self.redis_client.set(
            self._resolution_id_key(record.resolution_id),
            payload,
            ex=ttl_seconds,
        )

        owner_key = self._active_owner_key(
            provider_call_id=record.provider_call_id,
            conversation_id=record.conversation_id,
        )
        if owner_key is not None:
            self.redis_client.set(owner_key, str(record.resolution_id), ex=ttl_seconds)

    def get(self, resolution_id: UUID) -> PatientResolutionRecord | None:
        raw_value = self.redis_client.get(self._resolution_id_key(resolution_id))
        if raw_value is None:
            return None

        if isinstance(raw_value, bytes):
            raw_value = raw_value.decode("utf-8")

        return self._deserialize(raw_value)

    def update(self, record: PatientResolutionRecord, *, ttl_seconds: int) -> None:
        self.save(record, ttl_seconds=ttl_seconds)

    def _resolution_id_key(self, resolution_id: UUID) -> str:
        return f"{self.key_prefix}:id:{resolution_id}"

    def _active_owner_key(
        self,
        *,
        provider_call_id: str | None,
        conversation_id: UUID | None,
    ) -> str | None:
        if provider_call_id:
            return f"{self.key_prefix}:call:{provider_call_id}:active"

        if conversation_id is not None:
            return f"{self.key_prefix}:conversation:{conversation_id}:active"

        return None

    def _serialize(self, record: PatientResolutionRecord) -> str:
        return json.dumps(
            {
                "resolution_id": str(record.resolution_id),
                "patient_id": str(record.patient_id),
                "match_status": record.match_status.value,
                "provider_call_id": record.provider_call_id,
                "conversation_id": (
                    str(record.conversation_id) if record.conversation_id is not None else None
                ),
                "confirmed": record.confirmed,
                "created_at": self._datetime_to_string(record.created_at),
            },
        )

    def _deserialize(self, raw_value: str) -> PatientResolutionRecord:
        data = json.loads(raw_value)
        conversation_id = data.get("conversation_id")

        return PatientResolutionRecord(
            resolution_id=UUID(data["resolution_id"]),
            patient_id=UUID(data["patient_id"]),
            match_status=PatientResolutionMatchStatus(data["match_status"]),
            provider_call_id=data.get("provider_call_id"),
            conversation_id=UUID(conversation_id) if conversation_id else None,
            confirmed=bool(data["confirmed"]),
            created_at=datetime.fromisoformat(data["created_at"]),
        )

    def _datetime_to_string(self, value: datetime) -> str:
        if value.tzinfo is None:
            return value.isoformat()

        return value.astimezone(UTC).isoformat()
