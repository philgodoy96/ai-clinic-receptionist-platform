from collections.abc import Sequence
from datetime import datetime
from uuid import UUID

from redis.exceptions import RedisError

from app.domain.scheduling.appointment_holds import AppointmentHold
from app.repositories.appointment_holds import AppointmentHoldRepository


class AppointmentHoldServiceError(Exception):
    """Base exception for appointment hold service errors."""


class InvalidAppointmentHoldOwnerError(AppointmentHoldServiceError):
    """Raised when a hold owner is missing or invalid."""


class InvalidAppointmentHoldWindowError(AppointmentHoldServiceError):
    """Raised when a hold time window is invalid."""


class AppointmentSlotAlreadyHeldError(AppointmentHoldServiceError):
    """Raised when a slot already has an active hold."""


class AppointmentHoldNotFoundError(AppointmentHoldServiceError):
    """Raised when a hold does not exist or has expired."""


class AppointmentHoldMismatchError(AppointmentHoldServiceError):
    """Raised when a provided hold_id does not match the active hold."""


class AppointmentHoldOwnershipError(AppointmentHoldServiceError):
    """Raised when a hold belongs to another owner."""


class AppointmentHoldStoreUnavailableError(AppointmentHoldServiceError):
    """Raised when the hold store cannot be reached for a correctness-critical operation."""


class AppointmentHoldService:
    def __init__(
        self,
        *,
        repository: AppointmentHoldRepository,
        ttl_seconds: int,
    ) -> None:
        if ttl_seconds <= 0:
            raise ValueError("ttl_seconds must be greater than zero")

        self.repository = repository
        self.ttl_seconds = ttl_seconds

    def create_hold(
        self,
        *,
        availability_slot_id: UUID,
        doctor_id: UUID,
        start_time: datetime,
        end_time: datetime,
        owner_id: str,
    ) -> AppointmentHold:
        normalized_owner_id = owner_id.strip()

        if not normalized_owner_id:
            raise InvalidAppointmentHoldOwnerError("owner_id is required")

        if end_time <= start_time:
            raise InvalidAppointmentHoldWindowError("end_time must be greater than start_time")

        hold = AppointmentHold.create(
            availability_slot_id=availability_slot_id,
            doctor_id=doctor_id,
            start_time=start_time,
            end_time=end_time,
            owner_id=normalized_owner_id,
        )

        try:
            created = self.repository.create(hold, ttl_seconds=self.ttl_seconds)
        except RedisError as exc:
            raise AppointmentHoldStoreUnavailableError(
                "appointment hold store is unavailable",
            ) from exc

        if not created:
            raise AppointmentSlotAlreadyHeldError("slot already has an active hold")

        return hold

    def get_hold_by_id(self, hold_id: UUID) -> AppointmentHold | None:
        return self.repository.get_by_hold_id(hold_id)

    def release_hold_by_id(self, *, hold_id: UUID, owner_id: str) -> None:
        hold = self.repository.get_by_hold_id(hold_id)

        if hold is None:
            return

        self.release_hold(
            doctor_id=hold.doctor_id,
            start_time=hold.start_time,
            owner_id=owner_id,
        )

    def validate_hold(
        self,
        *,
        hold_id: UUID,
        doctor_id: UUID,
        start_time: datetime,
        owner_id: str,
    ) -> AppointmentHold:
        hold = self.repository.get(doctor_id=doctor_id, start_time=start_time)

        if hold is None:
            raise AppointmentHoldNotFoundError("appointment hold was not found or expired")

        if hold.hold_id != hold_id:
            raise AppointmentHoldMismatchError("appointment hold id does not match")

        if hold.owner_id != owner_id:
            raise AppointmentHoldOwnershipError("appointment hold belongs to another owner")

        return hold

    def release_hold(
        self,
        *,
        doctor_id: UUID,
        start_time: datetime,
        owner_id: str,
    ) -> None:
        hold = self.repository.get(doctor_id=doctor_id, start_time=start_time)

        if hold is None:
            return

        if hold.owner_id != owner_id:
            raise AppointmentHoldOwnershipError("appointment hold belongs to another owner")

        self.repository.delete(doctor_id=doctor_id, start_time=start_time)

    def find_held_availability_slot_ids(
        self,
        *,
        doctor_id: UUID,
        slots: Sequence[tuple[UUID, datetime]],
    ) -> set[UUID]:
        try:
            return self.repository.find_held_availability_slot_ids(
                doctor_id=doctor_id,
                slots=slots,
            )
        except RedisError as exc:
            raise AppointmentHoldStoreUnavailableError(
                "appointment hold store is unavailable",
            ) from exc
