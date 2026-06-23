from __future__ import annotations

import json
import logging
from collections.abc import Sequence
from uuid import UUID, uuid4

from fastapi.testclient import TestClient

from app.core.logging import JsonLogFormatter
from app.core.request_context import (
    get_correlation_id,
    get_request_id,
    reset_request_context,
    set_request_context,
)
from app.domain.audit.enums import AuditActorType, AuditEventOutcome, AuditEventType
from app.main import create_app
from app.models.audit import AuditLog
from app.services.audit_log_pagination import AuditLogCursor
from app.services.audit_logs import AuditLogCreate, AuditLogService


def test_request_correlation_headers_are_generated() -> None:
    app = create_app()

    with TestClient(app) as client:
        response = client.get("/health")

    assert response.status_code == 200
    assert response.headers["X-Request-ID"]
    assert response.headers["X-Correlation-ID"]


def test_request_correlation_preserves_incoming_headers() -> None:
    app = create_app()

    with TestClient(app) as client:
        response = client.get(
            "/health",
            headers={
                "X-Request-ID": "request-123",
                "X-Correlation-ID": "correlation-456",
            },
        )

    assert response.status_code == 200
    assert response.headers["X-Request-ID"] == "request-123"
    assert response.headers["X-Correlation-ID"] == "correlation-456"


def test_request_context_can_be_set_and_reset() -> None:
    tokens = set_request_context(
        request_id="request-123",
        correlation_id="correlation-456",
    )

    assert get_request_id() == "request-123"
    assert get_correlation_id() == "correlation-456"

    reset_request_context(tokens)

    assert get_request_id() is None
    assert get_correlation_id() is None


def test_audit_log_service_uses_current_request_id_when_missing() -> None:
    repository = FakeAuditLogRepository()
    service = AuditLogService(repository=repository)
    tokens = set_request_context(
        request_id="request-123",
        correlation_id="correlation-456",
    )

    try:
        audit_log = service.record(
            AuditLogCreate(
                event_type=AuditEventType.APPOINTMENT_BOOKING_CONFIRMED,
                outcome=AuditEventOutcome.SUCCESS,
                actor_type=AuditActorType.API,
                source="scheduling_api",
                appointment_id=uuid4(),
            ),
        )
    finally:
        reset_request_context(tokens)

    assert audit_log.request_id == "request-123"


def test_json_log_formatter_includes_request_context() -> None:
    tokens = set_request_context(
        request_id="request-123",
        correlation_id="correlation-456",
    )
    formatter = JsonLogFormatter()
    record = logging.LogRecord(
        name="test",
        level=logging.INFO,
        pathname=__file__,
        lineno=1,
        msg="hello",
        args=(),
        exc_info=None,
    )

    try:
        payload = json.loads(formatter.format(record))
    finally:
        reset_request_context(tokens)

    assert payload["request_id"] == "request-123"
    assert payload["correlation_id"] == "correlation-456"
    assert payload["message"] == "hello"


class FakeAuditLogRepository:
    def __init__(self) -> None:
        self.audit_logs: list[AuditLog] = []

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
        return self.audit_logs[:limit]
