from collections.abc import Sequence
from datetime import datetime
from typing import Protocol
from uuid import UUID

from app.domain.scheduling.appointment_holds import AppointmentHold


class AppointmentHoldRepository(Protocol):
    def create(self, hold: AppointmentHold, ttl_seconds: int) -> bool:
        raise NotImplementedError

    def get(self, *, doctor_id: UUID, start_time: datetime) -> AppointmentHold | None:
        raise NotImplementedError

    def get_by_hold_id(self, hold_id: UUID) -> AppointmentHold | None:
        raise NotImplementedError

    def delete(self, *, doctor_id: UUID, start_time: datetime) -> None:
        raise NotImplementedError

    def find_held_availability_slot_ids(
        self,
        *,
        doctor_id: UUID,
        slots: Sequence[tuple[UUID, datetime]],
    ) -> set[UUID]:
        raise NotImplementedError
