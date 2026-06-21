from __future__ import annotations

from collections.abc import Generator
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient

from app.api.dependencies import get_human_escalation_service
from app.db.session import get_db
from app.domain.human_escalations import (
    HumanEscalationPriority,
    HumanEscalationReason,
    HumanEscalationSource,
    HumanEscalationStatus,
)
from app.main import create_app
from app.models.human_escalation import HumanEscalation
from app.services.clock import FixedClock
from app.services.human_escalations import HumanEscalationService
from tests.test_human_escalations import FakeHumanEscalationRepository


@pytest.fixture()
def empty_escalation_client() -> Generator[TestClient, None, None]:
    app = create_app()
    service = HumanEscalationService(repository=FakeHumanEscalationRepository())

    def override_human_escalation_service() -> HumanEscalationService:
        return service

    app.dependency_overrides[get_human_escalation_service] = override_human_escalation_service

    with TestClient(app) as test_client:
        yield test_client

    app.dependency_overrides.clear()


@pytest.fixture()
def escalation_client() -> Generator[tuple[TestClient, UUID], None, None]:
    app = create_app()
    fake_db = FakeDatabaseSession()
    repository = FakeHumanEscalationRepository()
    service = HumanEscalationService(repository=repository)
    conversation_id = uuid4()
    escalation = service.create_or_get_active_escalation(
        conversation_id=conversation_id,
        reason=HumanEscalationReason.USER_REQUESTED_HUMAN,
        summary="User asked for a human.",
    )

    def override_human_escalation_service() -> HumanEscalationService:
        return service

    def override_db() -> Generator[FakeDatabaseSession, None, None]:
        yield fake_db

    app.dependency_overrides[get_human_escalation_service] = override_human_escalation_service
    app.dependency_overrides[get_db] = override_db

    with TestClient(app) as test_client:
        yield test_client, escalation.id

    app.dependency_overrides.clear()


@pytest.fixture()
def filtered_escalation_client() -> Generator[TestClient, None, None]:
    app = create_app()
    repository = FakeHumanEscalationRepository()
    service = HumanEscalationService(repository=repository)
    service.create_or_get_active_escalation(
        conversation_id=uuid4(),
        reason=HumanEscalationReason.USER_REQUESTED_HUMAN,
    )
    service.create_or_get_active_escalation(
        conversation_id=uuid4(),
        reason=HumanEscalationReason.MEDICAL_EMERGENCY,
    )
    service.create_or_get_active_escalation(
        conversation_id=uuid4(),
        reason=HumanEscalationReason.REPEATED_FALLBACK,
    )

    def override_human_escalation_service() -> HumanEscalationService:
        return service

    app.dependency_overrides[get_human_escalation_service] = override_human_escalation_service

    with TestClient(app) as test_client:
        yield test_client

    app.dependency_overrides.clear()


@pytest.fixture()
def assignment_filter_client() -> Generator[TestClient, None, None]:
    app = create_app()
    now = datetime(2026, 7, 1, 12, 0, tzinfo=UTC)
    repository = FakeHumanEscalationRepository(
        [
            HumanEscalation(
                id=uuid4(),
                conversation_id=uuid4(),
                status=HumanEscalationStatus.ACKNOWLEDGED,
                reason=HumanEscalationReason.USER_REQUESTED_HUMAN,
                priority=HumanEscalationPriority.HIGH,
                source=HumanEscalationSource.CHAT,
                assigned_to="staff-1",
                assigned_at=now - timedelta(hours=1),
                due_at=now + timedelta(hours=3),
                created_at=now - timedelta(hours=2),
                updated_at=now - timedelta(hours=1),
            ),
            HumanEscalation(
                id=uuid4(),
                conversation_id=uuid4(),
                status=HumanEscalationStatus.OPEN,
                reason=HumanEscalationReason.NO_PROGRESS,
                priority=HumanEscalationPriority.NORMAL,
                source=HumanEscalationSource.CHAT,
                created_at=now - timedelta(hours=3),
                updated_at=now - timedelta(hours=3),
            ),
            HumanEscalation(
                id=uuid4(),
                conversation_id=uuid4(),
                status=HumanEscalationStatus.OPEN,
                reason=HumanEscalationReason.MEDICAL_EMERGENCY,
                priority=HumanEscalationPriority.URGENT,
                source=HumanEscalationSource.CHAT,
                due_at=now - timedelta(minutes=5),
                created_at=now - timedelta(hours=4),
                updated_at=now - timedelta(hours=4),
            ),
            HumanEscalation(
                id=uuid4(),
                conversation_id=uuid4(),
                status=HumanEscalationStatus.RESOLVED,
                reason=HumanEscalationReason.REPEATED_FALLBACK,
                priority=HumanEscalationPriority.NORMAL,
                source=HumanEscalationSource.CHAT,
                assigned_to="staff-2",
                due_at=now - timedelta(hours=1),
                created_at=now - timedelta(hours=5),
                updated_at=now - timedelta(minutes=30),
            ),
        ],
    )
    service = HumanEscalationService(
        repository=repository,
        clock=FixedClock(current_time=now),
    )

    def override_human_escalation_service() -> HumanEscalationService:
        return service

    app.dependency_overrides[get_human_escalation_service] = override_human_escalation_service

    with TestClient(app) as test_client:
        yield test_client

    app.dependency_overrides.clear()


def test_list_human_escalations_returns_empty_items(
    empty_escalation_client: TestClient,
) -> None:
    response = empty_escalation_client.get("/api/v1/human-escalations")

    assert response.status_code == 200

    body = response.json()

    assert body["items"] == []
    assert body["next_cursor"] is None


def test_list_human_escalations_returns_created_escalation(
    escalation_client: tuple[TestClient, UUID],
) -> None:
    client, escalation_id = escalation_client

    response = client.get("/api/v1/human-escalations")

    assert response.status_code == 200

    body = response.json()

    assert len(body["items"]) == 1
    assert body["items"][0]["id"] == str(escalation_id)
    assert body["items"][0]["status"] == "open"
    assert body["items"][0]["reason"] == "user_requested_human"
    assert body["items"][0]["priority"] == "high"
    assert body["next_cursor"] is None


def test_list_human_escalations_filters_by_status_reason_and_priority(
    filtered_escalation_client: TestClient,
) -> None:
    response = filtered_escalation_client.get(
        "/api/v1/human-escalations",
        params={
            "status": "open",
            "reason": "user_requested_human",
            "priority": "high",
        },
    )

    assert response.status_code == 200

    body = response.json()

    assert len(body["items"]) == 1
    assert body["items"][0]["status"] == "open"
    assert body["items"][0]["reason"] == "user_requested_human"
    assert body["items"][0]["priority"] == "high"


def test_list_human_escalations_supports_cursor_pagination(
    filtered_escalation_client: TestClient,
) -> None:
    first_page = filtered_escalation_client.get(
        "/api/v1/human-escalations",
        params={"limit": 2},
    )

    assert first_page.status_code == 200

    first_body = first_page.json()

    assert len(first_body["items"]) == 2
    assert first_body["next_cursor"] is not None

    second_page = filtered_escalation_client.get(
        "/api/v1/human-escalations",
        params={"limit": 2, "cursor": first_body["next_cursor"]},
    )

    assert second_page.status_code == 200

    second_body = second_page.json()

    assert len(second_body["items"]) == 1
    assert second_body["next_cursor"] is None
    assert second_body["items"][0]["id"] != first_body["items"][0]["id"]


def test_get_human_escalation_returns_escalation_by_id(
    escalation_client: tuple[TestClient, UUID],
) -> None:
    client, escalation_id = escalation_client

    response = client.get(f"/api/v1/human-escalations/{escalation_id}")

    assert response.status_code == 200

    body = response.json()

    assert body["id"] == str(escalation_id)
    assert body["summary"] == "User asked for a human."
    assert body["created_at"]
    assert body["updated_at"]


def test_acknowledge_human_escalation_transitions_open_to_acknowledged(
    escalation_client: tuple[TestClient, UUID],
) -> None:
    client, escalation_id = escalation_client

    response = client.post(
        f"/api/v1/human-escalations/{escalation_id}/acknowledge",
        json={"acknowledged_by": "demo_staff"},
    )

    assert response.status_code == 200

    body = response.json()

    assert body["status"] == "acknowledged"
    assert body["acknowledged_by"] == "demo_staff"
    assert body["acknowledged_at"] is not None


def test_resolve_human_escalation_transitions_acknowledged_to_resolved(
    escalation_client: tuple[TestClient, UUID],
) -> None:
    client, escalation_id = escalation_client

    acknowledge_response = client.post(
        f"/api/v1/human-escalations/{escalation_id}/acknowledge",
        json={"acknowledged_by": "demo_staff"},
    )
    assert acknowledge_response.status_code == 200

    response = client.post(
        f"/api/v1/human-escalations/{escalation_id}/resolve",
        json={
            "resolved_by": "demo_staff",
            "resolution_notes": "Called patient and resolved scheduling issue.",
        },
    )

    assert response.status_code == 200

    body = response.json()

    assert body["status"] == "resolved"
    assert body["resolved_by"] == "demo_staff"
    assert body["resolution_notes"] == "Called patient and resolved scheduling issue."
    assert body["resolved_at"] is not None


def test_get_human_escalation_returns_standardized_not_found(
    empty_escalation_client: TestClient,
) -> None:
    response = empty_escalation_client.get(f"/api/v1/human-escalations/{uuid4()}")

    assert response.status_code == 404

    body = response.json()

    assert body["error"]["code"] == "human_escalation_not_found"
    assert body["error"]["message"] == "Human escalation was not found."
    assert body["error"]["request_id"] is not None


def test_acknowledge_resolved_human_escalation_returns_standardized_conflict(
    escalation_client: tuple[TestClient, UUID],
) -> None:
    client, escalation_id = escalation_client

    client.post(
        f"/api/v1/human-escalations/{escalation_id}/resolve",
        json={
            "resolved_by": "demo_staff",
            "resolution_notes": "Resolved.",
        },
    )

    response = client.post(
        f"/api/v1/human-escalations/{escalation_id}/acknowledge",
        json={"acknowledged_by": "demo_staff"},
    )

    assert response.status_code == 409

    body = response.json()

    assert body["error"]["code"] == "invalid_human_escalation_transition"
    assert body["error"]["message"] == "Only open escalations can be acknowledged."


def test_post_acknowledge_unknown_human_escalation_returns_standardized_not_found(
    empty_escalation_client: TestClient,
) -> None:
    response = empty_escalation_client.post(
        f"/api/v1/human-escalations/{uuid4()}/acknowledge",
        json={"acknowledged_by": "demo_staff"},
    )

    assert response.status_code == 404

    body = response.json()

    assert body["error"]["code"] == "human_escalation_not_found"
    assert body["error"]["message"] == "Human escalation was not found."


def test_post_resolve_unknown_human_escalation_returns_standardized_not_found(
    empty_escalation_client: TestClient,
) -> None:
    response = empty_escalation_client.post(
        f"/api/v1/human-escalations/{uuid4()}/resolve",
        json={
            "resolved_by": "demo_staff",
            "resolution_notes": "Resolved.",
        },
    )

    assert response.status_code == 404

    body = response.json()

    assert body["error"]["code"] == "human_escalation_not_found"
    assert body["error"]["message"] == "Human escalation was not found."


def test_post_assign_returns_assigned_escalation(
    escalation_client: tuple[TestClient, UUID],
) -> None:
    client, escalation_id = escalation_client

    response = client.post(
        f"/api/v1/human-escalations/{escalation_id}/assign",
        json={"assigned_to": "demo_staff"},
    )

    assert response.status_code == 200

    body = response.json()

    assert body["status"] == "acknowledged"
    assert body["assigned_to"] == "demo_staff"
    assert body["assigned_at"] is not None
    assert body["due_at"] is not None
    assert body["is_overdue"] is False


def test_post_unassign_returns_unassigned_escalation(
    escalation_client: tuple[TestClient, UUID],
) -> None:
    client, escalation_id = escalation_client

    assign_response = client.post(
        f"/api/v1/human-escalations/{escalation_id}/assign",
        json={"assigned_to": "demo_staff"},
    )
    assert assign_response.status_code == 200
    due_at = assign_response.json()["due_at"]

    response = client.post(f"/api/v1/human-escalations/{escalation_id}/unassign")

    assert response.status_code == 200

    body = response.json()

    assert body["assigned_to"] is None
    assert body["assigned_at"] is None
    assert body["due_at"] == due_at


def test_post_assign_unknown_human_escalation_returns_standardized_not_found(
    empty_escalation_client: TestClient,
) -> None:
    response = empty_escalation_client.post(
        f"/api/v1/human-escalations/{uuid4()}/assign",
        json={"assigned_to": "demo_staff"},
    )

    assert response.status_code == 404

    body = response.json()

    assert body["error"]["code"] == "human_escalation_not_found"
    assert body["error"]["message"] == "Human escalation was not found."
    assert body["error"]["request_id"] is not None


def test_post_assign_resolved_human_escalation_returns_standardized_conflict(
    escalation_client: tuple[TestClient, UUID],
) -> None:
    client, escalation_id = escalation_client

    client.post(
        f"/api/v1/human-escalations/{escalation_id}/resolve",
        json={
            "resolved_by": "demo_staff",
            "resolution_notes": "Resolved.",
        },
    )

    response = client.post(
        f"/api/v1/human-escalations/{escalation_id}/assign",
        json={"assigned_to": "demo_staff"},
    )

    assert response.status_code == 409

    body = response.json()

    assert body["error"]["code"] == "invalid_human_escalation_transition"
    assert body["error"]["request_id"] is not None


def test_assign_same_staff_twice_is_idempotent(
    escalation_client: tuple[TestClient, UUID],
) -> None:
    client, escalation_id = escalation_client

    first = client.post(
        f"/api/v1/human-escalations/{escalation_id}/assign",
        json={"assigned_to": "demo_staff"},
    )
    assert first.status_code == 200

    second = client.post(
        f"/api/v1/human-escalations/{escalation_id}/assign",
        json={"assigned_to": "demo_staff"},
    )

    assert second.status_code == 200

    first_body = first.json()
    second_body = second.json()

    assert second_body["assigned_at"] == first_body["assigned_at"]
    assert second_body["due_at"] == first_body["due_at"]
    assert second_body["updated_at"] == first_body["updated_at"]


def test_assign_different_staff_reassigns(
    escalation_client: tuple[TestClient, UUID],
) -> None:
    client, escalation_id = escalation_client

    first = client.post(
        f"/api/v1/human-escalations/{escalation_id}/assign",
        json={"assigned_to": "demo_staff"},
    )
    assert first.status_code == 200

    second = client.post(
        f"/api/v1/human-escalations/{escalation_id}/assign",
        json={"assigned_to": "other_staff"},
    )

    assert second.status_code == 200

    body = second.json()

    assert body["assigned_to"] == "other_staff"
    assert body["due_at"] is not None


def test_list_human_escalations_assignment_filters(
    assignment_filter_client: TestClient,
) -> None:
    assigned_to_response = assignment_filter_client.get(
        "/api/v1/human-escalations",
        params={"assigned_to": "staff-1"},
    )
    unassigned_response = assignment_filter_client.get(
        "/api/v1/human-escalations",
        params={"unassigned": True},
    )
    overdue_response = assignment_filter_client.get(
        "/api/v1/human-escalations",
        params={"overdue": True},
    )

    assert assigned_to_response.status_code == 200
    assert unassigned_response.status_code == 200
    assert overdue_response.status_code == 200

    assigned_body = assigned_to_response.json()
    unassigned_body = unassigned_response.json()
    overdue_body = overdue_response.json()

    assert len(assigned_body["items"]) == 1
    assert assigned_body["items"][0]["assigned_to"] == "staff-1"

    assert len(unassigned_body["items"]) == 2
    assert all(item["assigned_to"] is None for item in unassigned_body["items"])

    assert len(overdue_body["items"]) == 1
    assert overdue_body["items"][0]["reason"] == "medical_emergency"
    assert overdue_body["items"][0]["is_overdue"] is True


class FakeDatabaseSession:
    def __init__(self) -> None:
        self.committed = False
        self.rolled_back = False

    def commit(self) -> None:
        self.committed = True

    def rollback(self) -> None:
        self.rolled_back = True

    def refresh(self, instance: object) -> None:
        return None
