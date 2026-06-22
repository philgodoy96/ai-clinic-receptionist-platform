from __future__ import annotations

from collections.abc import Generator
from datetime import UTC, datetime
from typing import Any, cast
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from redis.exceptions import RedisError

from app.adapters.retell.scheduling_tools import RetellSchedulingToolAdapter
from app.api.dependencies import (
    get_appointment_hold_service,
    get_chat_receptionist_service,
    get_demo_guardrail_service,
    get_email_job_dispatch_publisher,
    get_email_job_service,
    get_retell_scheduling_tool_adapter,
)
from app.core.config import Settings
from app.db.session import get_db
from app.domain.jobs.enums import EmailJobStatus, EmailJobType
from app.main import create_app
from app.models.email_jobs import EmailJob
from app.services.chat_receptionist import ChatReceptionistService
from app.services.clock import FixedClock
from app.services.conversations import ConversationService
from app.services.demo_guardrails import DemoGuardrailService
from app.services.email_jobs import (
    AppointmentConfirmationEmailJobCreate,
    EmailJobService,
)
from tests.test_api_errors import EmptySchedulingService
from tests.test_chat_api import FakeDatabaseSession, FakeEmailJobService
from tests.test_chat_booking_confirmation_flow import (
    FULL_IDENTITY_WITH_CONFIRM,
    create_jane_doe_patient,
)
from tests.test_chat_receptionist_service import (
    FakeAppointmentHoldService,
    create_chat_receptionist_service,
)
from tests.test_conversations import FakeConversationRepository
from tests.test_scheduling_services import (
    create_demo_scheduling_service_with_emily_july_availability,
)


def make_guardrail_settings(**overrides: Any) -> Settings:
    values: dict[str, Any] = {
        "PUBLIC_DEMO_GUARDRAILS_ENABLED": True,
        "DEMO_CHAT_MESSAGES_PER_MINUTE_PER_IP": 100,
        "DEMO_CHAT_MESSAGES_PER_DAY_PER_IP": 100,
        "DEMO_RETELL_TOOL_CALLS_PER_MINUTE_PER_IP": 2,
        "DEMO_RETELL_TOOL_CALLS_PER_DAY_PER_IP": 100,
        "DEMO_APPOINTMENTS_PER_DAY_PER_IP": 5,
        "DEMO_CONFIRMATION_EMAILS_PER_DAY_PER_IP": 5,
        "DEMO_GLOBAL_CHAT_MESSAGES_PER_DAY": 1000,
        "DEMO_GLOBAL_APPOINTMENTS_PER_DAY": 100,
        "DEMO_GLOBAL_CONFIRMATION_EMAILS_PER_DAY": 100,
    }
    values.update(overrides)
    return Settings(_env_file=None, **values)


@pytest.fixture()
def guarded_chat_client() -> Generator[tuple[TestClient, FakeRedisClient], None, None]:
    app, redis_client, _ = _create_guarded_chat_app(
        make_guardrail_settings(DEMO_CHAT_MESSAGES_PER_MINUTE_PER_IP=1),
    )

    with TestClient(app) as test_client:
        yield test_client, redis_client

    app.dependency_overrides.clear()


@pytest.fixture()
def guarded_retell_client() -> Generator[tuple[TestClient, FakeRedisClient], None, None]:
    app, redis_client = _create_guarded_retell_app(
        make_guardrail_settings(DEMO_RETELL_TOOL_CALLS_PER_MINUTE_PER_IP=1),
    )

    with TestClient(app) as test_client:
        yield test_client, redis_client

    app.dependency_overrides.clear()


def test_chat_over_limit_returns_429(
    guarded_chat_client: tuple[TestClient, FakeRedisClient],
) -> None:
    client, _ = guarded_chat_client

    first = client.post("/api/v1/chat/messages", json={"message": "Hi"})
    second = client.post("/api/v1/chat/messages", json={"message": "Hello again"})

    assert first.status_code == 200
    assert second.status_code == 429

    body = second.json()
    assert body["error"]["code"] == "demo_guardrail_limit_exceeded"
    assert body["error"]["details"]["limit_name"] == "chat_messages_per_minute_per_ip"
    assert body["error"]["request_id"] is not None


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


def test_redis_guardrail_failure_returns_503() -> None:
    app, _, _ = _create_guarded_chat_app(
        make_guardrail_settings(),
        redis_client=FailingRedisClient(),
    )

    with TestClient(app) as client:
        response = client.post("/api/v1/chat/messages", json={"message": "Hi"})

    app.dependency_overrides.clear()

    assert response.status_code == 503
    body = response.json()
    assert body["error"]["code"] == "demo_guardrail_store_unavailable"


def test_booking_success_increments_appointment_quota() -> None:
    redis_client = FakeRedisClient()
    app, _, _ = _create_guarded_chat_app(
        make_guardrail_settings(),
        redis_client=redis_client,
    )

    with TestClient(app) as client:
        conversation_id = _book_appointment_via_chat(client)

        assert conversation_id is not None

    app.dependency_overrides.clear()

    appointment_keys = [
        key for key in redis_client.values if "appointment" in key
    ]
    assert len(appointment_keys) == 2


def test_email_job_creation_increments_email_quota() -> None:
    redis_client = FakeRedisClient()
    app, _, email_jobs = _create_guarded_chat_app(
        make_guardrail_settings(),
        redis_client=redis_client,
        track_email_jobs=True,
    )

    with TestClient(app) as client:
        _book_appointment_via_chat(client)

    app.dependency_overrides.clear()

    email_keys = [key for key in redis_client.values if ":email:" in key]
    assert len(email_keys) == 2
    assert email_jobs is not None
    assert len(email_jobs.jobs) == 1


def test_failed_booking_does_not_increment_appointment_quota() -> None:
    redis_client = FakeRedisClient()
    app, _, _ = _create_guarded_chat_app(
        make_guardrail_settings(),
        redis_client=redis_client,
    )

    with TestClient(app) as client:
        response = client.post(
            "/api/v1/chat/messages",
            json={"message": "Dr. Emily Carter on 2026-07-02"},
        )

    app.dependency_overrides.clear()

    assert response.status_code == 200
    appointment_keys = [
        key for key in redis_client.values if "appointment" in key
    ]
    assert appointment_keys == []


def test_email_quota_exceeded_keeps_booking_and_skips_confirmation_email() -> None:
    redis_client = FakeRedisClient()
    app, _, email_jobs = _create_guarded_chat_app(
        make_guardrail_settings(
            DEMO_CONFIRMATION_EMAILS_PER_DAY_PER_IP=1,
            DEMO_CHAT_MESSAGES_PER_MINUTE_PER_IP=100,
        ),
        redis_client=redis_client,
        track_email_jobs=True,
    )
    day_bucket = "20260621"
    email_ip_key = f"demo_guardrail:email:ip:testclient:day:{day_bucket}"
    redis_client.values[email_ip_key] = 1

    with TestClient(app) as client:
        response = client.post(
            "/api/v1/chat/messages",
            json=_booking_messages(client),
        )

    app.dependency_overrides.clear()

    assert response.status_code == 200
    body = response.json()
    assert body["booking_confirmed"] is True
    assert body["confirmation_email_queued"] is False
    assert email_jobs is not None
    assert email_jobs.jobs == []


def _booking_messages(client: TestClient) -> dict[str, Any]:
    availability_response = client.post(
        "/api/v1/chat/messages",
        json={"message": "Dr. Emily Carter on 2026-07-02"},
    )
    conversation_id = availability_response.json()["conversation_id"]
    client.post(
        "/api/v1/chat/messages",
        json={
            "message": "I'll take 09:00",
            "conversation_id": conversation_id,
        },
    )
    return {
        "message": FULL_IDENTITY_WITH_CONFIRM,
        "conversation_id": conversation_id,
    }


def _book_appointment_via_chat(client: TestClient) -> str:
    response = client.post("/api/v1/chat/messages", json=_booking_messages(client))
    assert response.status_code == 200
    body = response.json()
    assert body["booking_confirmed"] is True
    return str(body["conversation_id"])


def _create_guarded_chat_app(
    settings: Settings,
    *,
    redis_client: FakeRedisClient | None = None,
    track_email_jobs: bool = False,
) -> tuple[Any, FakeRedisClient, FakeEmailJobService | None]:
    redis = redis_client or FakeRedisClient()
    app = create_app()
    repository = FakeConversationRepository()
    conversation_service = ConversationService(repository=repository)
    hold_service = FakeAppointmentHoldService()
    chat_service = create_chat_receptionist_service(
        conversations=conversation_service,
        scheduling=create_demo_scheduling_service_with_emily_july_availability(
            patients=[create_jane_doe_patient()],
        ),
        hold_service=hold_service,
    )
    tracked_email_jobs: FakeEmailJobService | None = (
        FakeEmailJobService() if track_email_jobs else None
    )
    email_jobs: EmailJobService = cast(
        EmailJobService,
        tracked_email_jobs or _NoopEmailJobService(),
    )
    db = FakeDatabaseSession()
    fixed_now = datetime(2026, 6, 21, 12, 30, 45, tzinfo=UTC)

    def override_guardrails() -> DemoGuardrailService:
        return DemoGuardrailService(
            redis_client=redis,
            settings=settings,
            clock=FixedClock(fixed_now),
        )

    def override_chat_service() -> ChatReceptionistService:
        return chat_service

    def override_db() -> Generator[FakeDatabaseSession, None, None]:
        yield db

    def override_hold_service() -> FakeAppointmentHoldService:
        return hold_service

    def override_email_job_service() -> EmailJobService:
        return email_jobs

    app.dependency_overrides[get_demo_guardrail_service] = override_guardrails
    app.dependency_overrides[get_chat_receptionist_service] = override_chat_service
    app.dependency_overrides[get_db] = override_db
    app.dependency_overrides[get_appointment_hold_service] = override_hold_service
    app.dependency_overrides[get_email_job_service] = override_email_job_service
    app.dependency_overrides[get_email_job_dispatch_publisher] = (
        lambda: _NoopDispatchPublisher()
    )

    return app, redis, tracked_email_jobs


def _create_guarded_retell_app(
    settings: Settings,
    *,
    redis_client: FakeRedisClient | None = None,
) -> tuple[Any, FakeRedisClient]:
    redis = redis_client or FakeRedisClient()
    app = create_app()
    fixed_now = datetime(2026, 6, 21, 12, 30, 45, tzinfo=UTC)

    def override_guardrails() -> DemoGuardrailService:
        return DemoGuardrailService(
            redis_client=redis,
            settings=settings,
            clock=FixedClock(fixed_now),
        )

    def override_adapter() -> RetellSchedulingToolAdapter:
        return RetellSchedulingToolAdapter(EmptySchedulingService())

    app.dependency_overrides[get_demo_guardrail_service] = override_guardrails
    app.dependency_overrides[get_retell_scheduling_tool_adapter] = override_adapter

    return app, redis


class FakeRedisClient:
    def __init__(self) -> None:
        self.values: dict[str, int] = {}
        self.expirations: dict[str, int] = {}

    def incr(self, key: str) -> int:
        self.values[key] = self.values.get(key, 0) + 1
        return self.values[key]

    def expire(self, key: str, seconds: int) -> bool:
        self.expirations[key] = seconds
        return True

    def get(self, key: str) -> str | None:
        value = self.values.get(key)
        if value is None:
            return None
        return str(value)


class FailingRedisClient(FakeRedisClient):
    def incr(self, key: str) -> int:
        raise RedisError("redis unavailable")


class _NoopEmailJobService:
    def enqueue_appointment_confirmation(
        self,
        payload: AppointmentConfirmationEmailJobCreate,
    ) -> EmailJob:
        return EmailJob(
            id=uuid4(),
            appointment_id=payload.appointment_id,
            patient_id=payload.patient_id,
            subject="Appointment confirmation",
            body="test",
            job_type=EmailJobType.APPOINTMENT_CONFIRMATION,
            status=EmailJobStatus.PENDING,
        )


class _NoopDispatchPublisher:
    def publish_email_job_ready(self, *, email_job_id: Any) -> None:
        return None
