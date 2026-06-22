from __future__ import annotations

from collections.abc import Generator
from datetime import UTC, datetime
from typing import cast
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.adapters.retell.appointment_hold_tools import RetellAppointmentHoldToolAdapter
from app.adapters.retell.scheduling_tools import RetellSchedulingToolAdapter
from app.api.dependencies import (
    get_appointment_hold_service,
    get_audit_log_service,
    get_demo_guardrail_service,
    get_retell_appointment_hold_tool_adapter,
    get_retell_scheduling_tool_adapter,
    get_scheduling_service,
)
from app.db.session import get_db
from app.domain.scheduling.enums import AvailabilitySlotStatus
from app.main import create_app
from app.models.scheduling import AvailabilitySlot
from app.services.appointment_holds import AppointmentHoldService
from app.services.audit_logs import AuditLogService
from app.services.clock import FixedClock
from app.services.demo_guardrails import DemoGuardrailService
from tests.demo_guardrail_support import (
    FIXED_GUARD_RAIL_NOW,
    FakeRedisClient,
    TrackingRedisClient,
    create_guarded_retell_app,
    make_guardrail_settings,
)
from tests.retell_webhook_support import (
    configure_retell_for_tests,
    install_fake_retell_verifier,
    make_secured_retell_settings,
    retell_request_headers,
)
from tests.test_api_errors import EmptySchedulingService
from tests.test_appointment_hold_api import (
    FakeAppointmentHoldRepository,
    FakeAuditLogService,
    FakeDatabaseSession,
    FakeSchedulingService,
)


@pytest.fixture()
def guarded_retell_client() -> Generator[tuple[TestClient, FakeRedisClient], None, None]:
    app, redis_client = create_guarded_retell_app(
        make_guardrail_settings(DEMO_RETELL_TOOL_CALLS_PER_MINUTE_PER_IP=1),
    )

    with TestClient(app) as test_client:
        yield test_client, redis_client

    app.dependency_overrides.clear()


def test_retell_tool_under_limit_works(
    guarded_retell_client: tuple[TestClient, FakeRedisClient],
) -> None:
    client, redis_client = guarded_retell_client

    response = client.post("/api/v1/retell/tools/list-specialties", json={})

    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is True
    assert len(redis_client.values) == 2


def test_retell_tool_over_limit_returns_429(
    guarded_retell_client: tuple[TestClient, FakeRedisClient],
) -> None:
    client, _ = guarded_retell_client

    first = client.post("/api/v1/retell/tools/list-specialties", json={})
    second = client.post("/api/v1/retell/tools/list-specialties", json={})

    assert first.status_code == 200
    assert second.status_code == 429

    body = second.json()
    assert body["error"]["code"] == "demo_guardrail_limit_exceeded"
    assert body["error"]["details"]["limit_name"] == "retell_tool_calls_per_minute_per_ip"
    assert body["error"]["request_id"] is not None


def test_unified_retell_tool_route_respects_guardrails(
    guarded_retell_client: tuple[TestClient, FakeRedisClient],
) -> None:
    client, _ = guarded_retell_client
    payload = {
        "provider_call_id": "retell-call-guardrail",
        "tool_name": "check_availability",
        "arguments": {
            "start_from": "2026-07-01T09:00:00Z",
            "start_to": "2026-07-01T12:00:00Z",
        },
    }

    first = client.post("/api/v1/retell/tools", json=payload)
    second = client.post("/api/v1/retell/tools", json=payload)

    assert first.status_code == 200
    assert second.status_code == 429
    assert second.json()["error"]["code"] == "demo_guardrail_limit_exceeded"


def test_local_retell_tools_pass_without_redis_guardrail_enforcement() -> None:
    redis_client = TrackingRedisClient()
    app, _ = create_guarded_retell_app(
        make_guardrail_settings(PUBLIC_DEMO_GUARDRAILS_ENABLED=False),
        redis_client=redis_client,
    )

    with TestClient(app) as client:
        response = client.post("/api/v1/retell/tools/list-specialties", json={})

    app.dependency_overrides.clear()

    assert response.status_code == 200
    assert redis_client.calls == []


def test_guarded_retell_uses_fake_adapter_not_real_providers(
    guarded_retell_client: tuple[TestClient, FakeRedisClient],
) -> None:
    client, redis_client = guarded_retell_client

    response = client.post("/api/v1/retell/tools/list-doctors", json={})

    assert response.status_code == 200
    assert isinstance(redis_client, FakeRedisClient)
    assert response.json()["ok"] is True


def test_invalid_signature_does_not_consume_retell_rate_limit() -> None:
    redis_client = TrackingRedisClient()
    guardrail_settings = make_guardrail_settings(DEMO_RETELL_TOOL_CALLS_PER_MINUTE_PER_IP=1)
    app = create_app()
    configure_retell_for_tests(
        app,
        settings=make_secured_retell_settings(
            PUBLIC_DEMO_GUARDRAILS_ENABLED=guardrail_settings.public_demo_guardrails_enabled,
            DEMO_RETELL_TOOL_CALLS_PER_MINUTE_PER_IP=(
                guardrail_settings.demo_retell_tool_calls_per_minute_per_ip
            ),
            DEMO_RETELL_TOOL_CALLS_PER_DAY_PER_IP=(
                guardrail_settings.demo_retell_tool_calls_per_day_per_ip
            ),
        ),
    )
    install_fake_retell_verifier(app)

    def override_guardrails() -> DemoGuardrailService:
        return DemoGuardrailService(
            redis_client=redis_client,
            settings=make_secured_retell_settings(
                PUBLIC_DEMO_GUARDRAILS_ENABLED=True,
                DEMO_RETELL_TOOL_CALLS_PER_MINUTE_PER_IP=1,
                DEMO_RETELL_TOOL_CALLS_PER_DAY_PER_IP=5,
            ),
            clock=FixedClock(FIXED_GUARD_RAIL_NOW),
        )

    app.dependency_overrides[get_demo_guardrail_service] = override_guardrails
    app.dependency_overrides[get_retell_scheduling_tool_adapter] = (
        lambda: RetellSchedulingToolAdapter(EmptySchedulingService())
    )

    with TestClient(app) as client:
        response = client.post(
            "/api/v1/retell/tools/list-specialties",
            content=b"{}",
            headers={
                "Content-Type": "application/json",
                **retell_request_headers(signature="v=1,d=invalid"),
            },
        )

    app.dependency_overrides.clear()

    assert response.status_code == 401
    assert response.json()["error"]["code"] == "retell_signature_invalid"
    assert redis_client.calls == []


def test_unverified_retell_hold_request_creates_no_hold_or_audit_entry() -> None:
    slot = AvailabilitySlot(
        id=uuid4(),
        doctor_id=uuid4(),
        start_time=datetime(2026, 7, 1, 10, 0, tzinfo=UTC),
        end_time=datetime(2026, 7, 1, 10, 30, tzinfo=UTC),
        status=AvailabilitySlotStatus.AVAILABLE,
    )
    hold_repository = FakeAppointmentHoldRepository()
    hold_service = AppointmentHoldService(repository=hold_repository, ttl_seconds=300)
    db = FakeDatabaseSession()
    audit_logs = FakeAuditLogService()
    scheduling_service = FakeSchedulingService(slot)

    app = create_app()
    configure_retell_for_tests(app, settings=make_secured_retell_settings())
    install_fake_retell_verifier(app)

    def override_scheduling_service() -> FakeSchedulingService:
        return scheduling_service

    def override_hold_service() -> AppointmentHoldService:
        return hold_service

    def override_db() -> Generator[FakeDatabaseSession, None, None]:
        yield db

    def override_audit_log_service() -> AuditLogService:
        return cast(AuditLogService, audit_logs)

    def override_retell_hold_adapter() -> RetellAppointmentHoldToolAdapter:
        return RetellAppointmentHoldToolAdapter(
            db=cast(Session, db),
            scheduling_service=scheduling_service,
            hold_service=hold_service,
            audit_logs=cast(AuditLogService, audit_logs),
        )

    app.dependency_overrides[get_scheduling_service] = override_scheduling_service
    app.dependency_overrides[get_appointment_hold_service] = override_hold_service
    app.dependency_overrides[get_db] = override_db
    app.dependency_overrides[get_audit_log_service] = override_audit_log_service
    app.dependency_overrides[get_retell_appointment_hold_tool_adapter] = (
        override_retell_hold_adapter
    )

    with TestClient(app) as client:
        response = client.post(
            "/api/v1/retell/tools/hold-appointment-slot",
            content=(
                f'{{"availability_slot_id":"{slot.id}","call_id":"retell-call-123"}}'.encode()
            ),
            headers={
                "Content-Type": "application/json",
                **retell_request_headers(signature="v=1,d=bad"),
            },
        )

    app.dependency_overrides.clear()

    assert response.status_code == 401
    assert response.json()["error"]["code"] == "retell_signature_invalid"
    assert hold_repository.holds == {}
    assert audit_logs.records == []
    assert db.committed is False
