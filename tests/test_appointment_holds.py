from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest

from app.domain.scheduling.appointment_holds import AppointmentHold
from app.repositories.redis.appointment_holds import RedisAppointmentHoldRepository
from app.services.appointment_holds import (
    AppointmentHoldMismatchError,
    AppointmentHoldNotFoundError,
    AppointmentHoldOwnershipError,
    AppointmentHoldService,
    AppointmentSlotAlreadyHeldError,
    InvalidAppointmentHoldOwnerError,
    InvalidAppointmentHoldWindowError,
)


def test_create_hold_stores_temporary_slot_reservation() -> None:
    repository = FakeAppointmentHoldRepository()
    service = AppointmentHoldService(repository=repository, ttl_seconds=300)
    slot_id = uuid4()
    doctor_id = uuid4()
    start_time = datetime(2026, 7, 1, 10, 0, tzinfo=UTC)
    end_time = start_time + timedelta(minutes=30)

    hold = service.create_hold(
        availability_slot_id=slot_id,
        doctor_id=doctor_id,
        start_time=start_time,
        end_time=end_time,
        owner_id="call-123",
    )

    stored = repository.get(doctor_id=doctor_id, start_time=start_time)

    assert stored is not None
    assert stored.hold_id == hold.hold_id
    assert stored.owner_id == "call-123"


def test_create_hold_rejects_missing_owner() -> None:
    service = AppointmentHoldService(
        repository=FakeAppointmentHoldRepository(),
        ttl_seconds=300,
    )
    start_time = datetime(2026, 7, 1, 10, 0, tzinfo=UTC)

    with pytest.raises(InvalidAppointmentHoldOwnerError):
        service.create_hold(
            availability_slot_id=uuid4(),
            doctor_id=uuid4(),
            start_time=start_time,
            end_time=start_time + timedelta(minutes=30),
            owner_id=" ",
        )


def test_create_hold_rejects_invalid_time_window() -> None:
    service = AppointmentHoldService(
        repository=FakeAppointmentHoldRepository(),
        ttl_seconds=300,
    )
    start_time = datetime(2026, 7, 1, 10, 0, tzinfo=UTC)

    with pytest.raises(InvalidAppointmentHoldWindowError):
        service.create_hold(
            availability_slot_id=uuid4(),
            doctor_id=uuid4(),
            start_time=start_time,
            end_time=start_time,
            owner_id="call-123",
        )


def test_create_hold_rejects_already_held_slot() -> None:
    repository = FakeAppointmentHoldRepository()
    service = AppointmentHoldService(repository=repository, ttl_seconds=300)
    slot_id = uuid4()
    doctor_id = uuid4()
    start_time = datetime(2026, 7, 1, 10, 0, tzinfo=UTC)
    end_time = start_time + timedelta(minutes=30)

    service.create_hold(
        availability_slot_id=slot_id,
        doctor_id=doctor_id,
        start_time=start_time,
        end_time=end_time,
        owner_id="call-123",
    )

    with pytest.raises(AppointmentSlotAlreadyHeldError):
        service.create_hold(
            availability_slot_id=slot_id,
            doctor_id=doctor_id,
            start_time=start_time,
            end_time=end_time,
            owner_id="call-456",
        )


def test_validate_hold_returns_hold_for_matching_owner_and_hold_id() -> None:
    service = AppointmentHoldService(
        repository=FakeAppointmentHoldRepository(),
        ttl_seconds=300,
    )
    doctor_id = uuid4()
    start_time = datetime(2026, 7, 1, 10, 0, tzinfo=UTC)
    hold = service.create_hold(
        availability_slot_id=uuid4(),
        doctor_id=doctor_id,
        start_time=start_time,
        end_time=start_time + timedelta(minutes=30),
        owner_id="call-123",
    )

    validated = service.validate_hold(
        hold_id=hold.hold_id,
        doctor_id=doctor_id,
        start_time=start_time,
        owner_id="call-123",
    )

    assert validated == hold


def test_validate_hold_rejects_missing_or_expired_hold() -> None:
    service = AppointmentHoldService(
        repository=FakeAppointmentHoldRepository(),
        ttl_seconds=300,
    )

    with pytest.raises(AppointmentHoldNotFoundError):
        service.validate_hold(
            hold_id=uuid4(),
            doctor_id=uuid4(),
            start_time=datetime(2026, 7, 1, 10, 0, tzinfo=UTC),
            owner_id="call-123",
        )


def test_validate_hold_rejects_mismatched_hold_id() -> None:
    service = AppointmentHoldService(
        repository=FakeAppointmentHoldRepository(),
        ttl_seconds=300,
    )
    doctor_id = uuid4()
    start_time = datetime(2026, 7, 1, 10, 0, tzinfo=UTC)
    service.create_hold(
        availability_slot_id=uuid4(),
        doctor_id=doctor_id,
        start_time=start_time,
        end_time=start_time + timedelta(minutes=30),
        owner_id="call-123",
    )

    with pytest.raises(AppointmentHoldMismatchError):
        service.validate_hold(
            hold_id=uuid4(),
            doctor_id=doctor_id,
            start_time=start_time,
            owner_id="call-123",
        )


def test_validate_hold_rejects_wrong_owner() -> None:
    service = AppointmentHoldService(
        repository=FakeAppointmentHoldRepository(),
        ttl_seconds=300,
    )
    doctor_id = uuid4()
    start_time = datetime(2026, 7, 1, 10, 0, tzinfo=UTC)
    hold = service.create_hold(
        availability_slot_id=uuid4(),
        doctor_id=doctor_id,
        start_time=start_time,
        end_time=start_time + timedelta(minutes=30),
        owner_id="call-123",
    )

    with pytest.raises(AppointmentHoldOwnershipError):
        service.validate_hold(
            hold_id=hold.hold_id,
            doctor_id=doctor_id,
            start_time=start_time,
            owner_id="call-456",
        )


def test_release_hold_deletes_hold_for_matching_owner() -> None:
    repository = FakeAppointmentHoldRepository()
    service = AppointmentHoldService(repository=repository, ttl_seconds=300)
    doctor_id = uuid4()
    start_time = datetime(2026, 7, 1, 10, 0, tzinfo=UTC)

    service.create_hold(
        availability_slot_id=uuid4(),
        doctor_id=doctor_id,
        start_time=start_time,
        end_time=start_time + timedelta(minutes=30),
        owner_id="call-123",
    )

    service.release_hold(
        doctor_id=doctor_id,
        start_time=start_time,
        owner_id="call-123",
    )

    assert repository.get(doctor_id=doctor_id, start_time=start_time) is None


def test_redis_repository_creates_reads_and_deletes_hold() -> None:
    redis_client = FakeRedisClient()
    repository = RedisAppointmentHoldRepository(redis_client)
    doctor_id = uuid4()
    start_time = datetime(2026, 7, 1, 10, 0, tzinfo=UTC)
    hold = AppointmentHold.create(
        availability_slot_id=uuid4(),
        doctor_id=doctor_id,
        start_time=start_time,
        end_time=start_time + timedelta(minutes=30),
        owner_id="call-123",
    )

    created = repository.create(hold, ttl_seconds=300)
    stored = repository.get(doctor_id=doctor_id, start_time=start_time)
    duplicate_created = repository.create(hold, ttl_seconds=300)

    repository.delete(doctor_id=doctor_id, start_time=start_time)

    assert created is True
    assert stored == hold
    assert duplicate_created is False
    assert repository.get(doctor_id=doctor_id, start_time=start_time) is None


class FakeAppointmentHoldRepository:
    def __init__(self) -> None:
        self.holds: dict[tuple[UUID, datetime], AppointmentHold] = {}

    def create(self, hold: AppointmentHold, ttl_seconds: int) -> bool:
        key = (hold.doctor_id, hold.start_time)

        if key in self.holds:
            return False

        self.holds[key] = hold

        return True

    def get(self, *, doctor_id: UUID, start_time: datetime) -> AppointmentHold | None:
        return self.holds.get((doctor_id, start_time))

    def get_by_hold_id(self, hold_id: UUID) -> AppointmentHold | None:
        for hold in self.holds.values():
            if hold.hold_id == hold_id:
                return hold

        return None

    def delete(self, *, doctor_id: UUID, start_time: datetime) -> None:
        self.holds.pop((doctor_id, start_time), None)


class FakeRedisClient:
    def __init__(self) -> None:
        self.values: dict[str, str] = {}

    def set(self, name: str, value: str, ex: int, nx: bool = False) -> bool:
        if nx and name in self.values:
            return False

        self.values[name] = value

        return True

    def get(self, name: str) -> str | None:
        return self.values.get(name)

    def delete(self, name: str) -> None:
        self.values.pop(name, None)
