from __future__ import annotations

from collections.abc import Generator, Sequence
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient

from app.api.dependencies import get_audit_log_service
from app.domain.audit.enums import AuditActorType, AuditEventOutcome, AuditEventType
from app.main import create_app
from app.models.audit import AuditLog
from app.services.audit_log_pagination import (
    AuditLogCursor,
    decode_audit_log_cursor,
    encode_audit_log_cursor,
)
from app.services.audit_logs import AuditLogService


def test_audit_log_cursor_round_trip() -> None:
    cursor = AuditLogCursor(
        created_at=datetime(2026, 7, 1, 10, 0, tzinfo=UTC),
        id=uuid4(),
    )

    encoded = encode_audit_log_cursor(cursor)
    decoded = decode_audit_log_cursor(encoded)

    assert decoded == cursor


def test_audit_log_service_returns_next_cursor() -> None:
    logs = create_audit_logs(count=3)
    service = AuditLogService(repository=FakeAuditLogRepository(logs))

    result = service.list_logs(limit=2)

    assert len(result.items) == 2
    assert result.next_cursor is not None


def test_audit_log_service_uses_cursor_for_next_page() -> None:
    logs = create_audit_logs(count=3)
    service = AuditLogService(repository=FakeAuditLogRepository(logs))
    first_page = service.list_logs(limit=2)

    second_page = service.list_logs(limit=2, cursor=first_page.next_cursor)

    assert len(second_page.items) == 1
    assert second_page.items[0].id == logs[2].id
    assert second_page.next_cursor is None


@pytest.fixture()
def client() -> Generator[TestClient, None, None]:
    app = create_app()
    logs = create_audit_logs(count=3)
    service = AuditLogService(repository=FakeAuditLogRepository(logs))

    def override_audit_log_service() -> AuditLogService:
        return service

    app.dependency_overrides[get_audit_log_service] = override_audit_log_service

    with TestClient(app) as test_client:
        yield test_client

    app.dependency_overrides.clear()


def test_list_audit_logs_endpoint_returns_items_and_cursor(client: TestClient) -> None:
    response = client.get("/api/v1/audit-logs", params={"limit": 2})

    assert response.status_code == 200

    body = response.json()

    assert len(body["items"]) == 2
    assert body["next_cursor"] is not None
    assert body["items"][0]["event_metadata"] == {"sequence": 0}


def test_list_audit_logs_endpoint_rejects_invalid_cursor(client: TestClient) -> None:
    response = client.get("/api/v1/audit-logs", params={"cursor": "not-a-valid-cursor"})

    assert response.status_code == 400
    assert response.json()["detail"] == "invalid audit log cursor"


class FakeAuditLogRepository:
    def __init__(self, audit_logs: Sequence[AuditLog]) -> None:
        self.audit_logs = list(audit_logs)

    def add(self, audit_log: AuditLog) -> AuditLog:
        self.audit_logs.append(audit_log)

        return audit_log

    def list_recent(
        self,
        *,
        limit: int,
        cursor: AuditLogCursor | None = None,
        event_type: AuditEventType | None = None,
        outcome: AuditEventOutcome | None = None,
        actor_type: AuditActorType | None = None,
        source: str | None = None,
        patient_id: UUID | None = None,
        appointment_id: UUID | None = None,
        call_id: str | None = None,
        conversation_id: str | None = None,
    ) -> Sequence[AuditLog]:
        logs = sorted(
            self.audit_logs,
            key=lambda item: (item.created_at, item.id),
            reverse=True,
        )

        if cursor is not None:
            logs = [
                item
                for item in logs
                if (item.created_at, item.id) < (cursor.created_at, cursor.id)
            ]

        if event_type is not None:
            logs = [item for item in logs if item.event_type == event_type]

        if outcome is not None:
            logs = [item for item in logs if item.outcome == outcome]

        if actor_type is not None:
            logs = [item for item in logs if item.actor_type == actor_type]

        if source is not None:
            logs = [item for item in logs if item.source == source]

        if patient_id is not None:
            logs = [item for item in logs if item.patient_id == patient_id]

        if appointment_id is not None:
            logs = [item for item in logs if item.appointment_id == appointment_id]

        if call_id is not None:
            logs = [item for item in logs if item.call_id == call_id]

        if conversation_id is not None:
            logs = [item for item in logs if item.conversation_id == conversation_id]

        return logs[:limit]


def create_audit_logs(*, count: int) -> list[AuditLog]:
    base_time = datetime(2026, 7, 1, 10, 0, tzinfo=UTC)

    return [
        AuditLog(
            id=uuid4(),
            event_type=AuditEventType.APPOINTMENT_BOOKING_CONFIRMED,
            outcome=AuditEventOutcome.SUCCESS,
            actor_type=AuditActorType.RETELL,
            actor_id=f"retell-call-{index}",
            source="retell_tool",
            call_id=f"retell-call-{index}",
            event_metadata={"sequence": index},
            created_at=base_time - timedelta(minutes=index),
        )
        for index in range(count)
    ]