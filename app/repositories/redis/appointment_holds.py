from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from app.domain.scheduling.appointment_holds import AppointmentHold


class RedisAppointmentHoldRepository:
    def __init__(
        self,
        redis_client: Any,
        *,
        key_prefix: str = "appointment_hold",
    ) -> None:
        self.redis_client = redis_client
        self.key_prefix = key_prefix

    def create(self, hold: AppointmentHold, ttl_seconds: int) -> bool:
        result = self.redis_client.set(
            self._key(doctor_id=hold.doctor_id, start_time=hold.start_time),
            self._serialize(hold),
            ex=ttl_seconds,
            nx=True,
        )

        if not result:
            return False

        self.redis_client.set(
            self._hold_id_key(hold.hold_id),
            self._serialize(hold),
            ex=ttl_seconds,
        )

        return True

    def get(self, *, doctor_id: UUID, start_time: datetime) -> AppointmentHold | None:
        raw_value = self.redis_client.get(
            self._key(doctor_id=doctor_id, start_time=start_time),
        )

        if raw_value is None:
            return None

        if isinstance(raw_value, bytes):
            raw_value = raw_value.decode("utf-8")

        return self._deserialize(raw_value)

    def get_by_hold_id(self, hold_id: UUID) -> AppointmentHold | None:
        raw_value = self.redis_client.get(self._hold_id_key(hold_id))

        if raw_value is None:
            return None

        if isinstance(raw_value, bytes):
            raw_value = raw_value.decode("utf-8")

        return self._deserialize(raw_value)

    def delete(self, *, doctor_id: UUID, start_time: datetime) -> None:
        hold = self.get(doctor_id=doctor_id, start_time=start_time)

        self.redis_client.delete(
            self._key(doctor_id=doctor_id, start_time=start_time),
        )

        if hold is not None:
            self.redis_client.delete(self._hold_id_key(hold.hold_id))

    def _key(self, *, doctor_id: UUID, start_time: datetime) -> str:
        return f"{self.key_prefix}:{doctor_id}:{self._datetime_to_string(start_time)}"

    def _hold_id_key(self, hold_id: UUID) -> str:
        return f"{self.key_prefix}:id:{hold_id}"

    def _serialize(self, hold: AppointmentHold) -> str:
        return json.dumps(
            {
                "hold_id": str(hold.hold_id),
                "availability_slot_id": str(hold.availability_slot_id),
                "doctor_id": str(hold.doctor_id),
                "start_time": self._datetime_to_string(hold.start_time),
                "end_time": self._datetime_to_string(hold.end_time),
                "owner_id": hold.owner_id,
                "created_at": self._datetime_to_string(hold.created_at),
            },
        )

    def _deserialize(self, raw_value: str) -> AppointmentHold:
        data = json.loads(raw_value)

        return AppointmentHold(
            hold_id=UUID(data["hold_id"]),
            availability_slot_id=UUID(data["availability_slot_id"]),
            doctor_id=UUID(data["doctor_id"]),
            start_time=datetime.fromisoformat(data["start_time"]),
            end_time=datetime.fromisoformat(data["end_time"]),
            owner_id=data["owner_id"],
            created_at=datetime.fromisoformat(data["created_at"]),
        )

    def _datetime_to_string(self, value: datetime) -> str:
        if value.tzinfo is None:
            return value.isoformat()

        return value.astimezone(UTC).isoformat()
