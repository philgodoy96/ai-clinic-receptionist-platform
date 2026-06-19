from __future__ import annotations

from collections.abc import Generator
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient

from app.adapters.retell.appointment_hold_tools import RetellAppointmentHoldToolAdapter
from app.api.dependencies import (
    get_appointment_hold_service,
    get_retell_appointment_hold_tool_adapter,
    get_scheduling_service,
)
from app.domain.scheduling.appointment_holds import AppointmentHold
from app.domain.scheduling.enums import AvailabilitySlotStatus
from app.main import create_app
from app.models.scheduling import AvailabilitySlot
from app.services.appointment_holds import AppointmentHoldService
from app.services.scheduling import AvailabilitySlotNotFoundError, AvailabilitySlotUnavailableError


@pytest.fixture()
def slot() -> AvailabilitySlot:
    start_time = datetime(2026, 7, 1, 10, 0, tzinfo=UTC)

    return AvailabilitySlot(
        id=uuid4(),
        doctor_id=uuid4(),
        start_time=start_time,
        end_time=start_time + timedelta(minutes=30),
        status=AvailabilitySlotStatus.AVAILABLE,
    )


@pytest.fixture()
def hold_service() -> AppointmentHoldService:
    return AppointmentHoldService(
        repository=FakeAppointmentHoldRepository(),
        ttl_seconds=300,
    )


@pytest.fixture()
def client(
    slot: AvailabilitySlot,
    hold_service: AppointmentHoldService,
) -> Generator[TestClient, None, None]:
    app = create_app()
    scheduling_service = FakeSchedulingService(slot)

    def override_scheduling_service() -> FakeSchedulingService:
        return scheduling_service

    def override_hold_service() -> AppointmentHoldService:
        return hold_service

    def override_retell_hold_adapter() -> RetellAppointmentHoldToolAdapter:
        return RetellAppointmentHoldToolAdapter(
            scheduling_service=scheduling_service,
            hold_service=hold_service,
        )

    app.dependency_overrides[get_scheduling_service] = override_scheduling_service
    app.dependency_overrides[get_appointment_hold_service] = override_hold_service
    app.dependency_overrides[
        get_retell_appointment_hold_tool_adapter
    ] = override_retell_hold_adapter

    with TestClient(app) as test_client:
        yield test_client

    app.dependency_overrides.clear()


def test_create_appointment_hold_returns_hold_response(
    client: TestClient,
    slot: AvailabilitySlot,
) -> None:
    response = client.post(
        "/api/v1/scheduling/appointment-holds",
        json={
            "availability_slot_id": str(slot.id),
            "owner_id": "chat-conversation-123",
        },
    )

    assert response.status_code == 201
    body = response.json()

    assert body["availability_slot_id"] == str(slot.id)
    assert body["doctor_id"] == str(slot.doctor_id)
    assert body["owner_id"] == "chat-conversation-123"
    assert body["expires_in_seconds"] == 300


def test_create_appointment_hold_rejects_duplicate_hold(
    client: TestClient,
    slot: AvailabilitySlot,
) -> None:
    payload = {
        "availability_slot_id": str(slot.id),
        "owner_id": "chat-conversation-123",
    }

    first_response = client.post("/api/v1/scheduling/appointment-holds", json=payload)
    second_response = client.post("/api/v1/scheduling/appointment-holds", json=payload)

    assert first_response.status_code == 201
    assert second_response.status_code == 409
    assert second_response.json()["detail"] == "slot already has an active hold"


def test_create_appointment_hold_returns_not_found_for_unknown_slot(
    client: TestClient,
) -> None:
    response = client.post(
        "/api/v1/scheduling/appointment-holds",
        json={
            "availability_slot_id": str(uuid4()),
            "owner_id": "chat-conversation-123",
        },
    )

    assert response.status_code == 404
    assert response.json()["detail"] == "availability slot was not found"


def test_retell_hold_tool_creates_hold_with_call_id(
    client: TestClient,
    slot: AvailabilitySlot,
) -> None:
    response = client.post(
        "/api/v1/retell/tools/hold-appointment-slot",
        json={
            "availability_slot_id": str(slot.id),
            "call_id": "retell-call-123",
        },
    )

    assert response.status_code == 200
    body = response.json()

    assert body["ok"] is True
    assert body["result"]["availability_slot_id"] == str(slot.id)
    assert body["result"]["expires_in_seconds"] == 300


def test_retell_hold_tool_rejects_missing_owner_context(
    client: TestClient,
    slot: AvailabilitySlot,
) -> None:
    response = client.post(
        "/api/v1/retell/tools/hold-appointment-slot",
        json={
            "availability_slot_id": str(slot.id),
        },
    )

    assert response.status_code == 200
    body = response.json()

    assert body["ok"] is False
    assert body["error_code"] == "missing_hold_owner"


def test_retell_hold_tool_returns_structured_error_for_duplicate_hold(
    client: TestClient,
    slot: AvailabilitySlot,
) -> None:
    payload = {
        "availability_slot_id": str(slot.id),
        "call_id": "retell-call-123",
    }

    first_response = client.post("/api/v1/retell/tools/hold-appointment-slot", json=payload)
    second_response = client.post("/api/v1/retell/tools/hold-appointment-slot", json=payload)

    assert first_response.status_code == 200
    assert first_response.json()["ok"] is True
    assert second_response.status_code == 200
    assert second_response.json()["ok"] is False
    assert second_response.json()["error_code"] == "slot_already_held"


class FakeSchedulingService:
    def __init__(self, slot: AvailabilitySlot) -> None:
        self.slot = slot

    def get_available_slot_for_hold(self, availability_slot_id: UUID) -> AvailabilitySlot:
        if availability_slot_id != self.slot.id:
            raise AvailabilitySlotNotFoundError

        if self.slot.status != AvailabilitySlotStatus.AVAILABLE:
            raise AvailabilitySlotUnavailableError

        return self.slot


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

    def delete(self, *, doctor_id: UUID, start_time: datetime) -> None:
        self.holds.pop((doctor_id, start_time), None)