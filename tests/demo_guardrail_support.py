from __future__ import annotations

from collections.abc import Generator
from datetime import UTC, datetime
from typing import Any, cast
from uuid import uuid4

from redis.exceptions import RedisError
from starlette.requests import Request

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
    AppointmentConfirmationEmailJobResult,
    EmailJobService,
)
from tests.test_api_errors import EmptySchedulingService
from tests.test_chat_api import FakeDatabaseSession, FakeEmailJobService
from tests.test_chat_booking_confirmation_flow import create_jane_doe_patient
from tests.test_chat_receptionist_service import (
    FakeAppointmentHoldService,
    create_chat_receptionist_service,
)
from tests.test_conversations import FakeConversationRepository
from tests.test_scheduling_services import (
    create_demo_scheduling_service_with_emily_july_availability,
)

FIXED_GUARD_RAIL_NOW = datetime(2026, 6, 21, 12, 30, 45, tzinfo=UTC)


def make_request(
    *,
    client_host: str | None = "203.0.113.10",
    headers: dict[str, str] | None = None,
) -> Request:
    raw_headers = [
        (name.lower().encode("utf-8"), value.encode("utf-8"))
        for name, value in (headers or {}).items()
    ]
    client = None if client_host is None else (client_host, 0)
    scope = {
        "type": "http",
        "method": "GET",
        "path": "/",
        "headers": raw_headers,
        "client": client,
    }
    return Request(scope)


def make_guardrail_settings(**overrides: Any) -> Settings:
    values: dict[str, Any] = {
        "PUBLIC_DEMO_GUARDRAILS_ENABLED": True,
        "DEMO_CHAT_MESSAGES_PER_MINUTE_PER_IP": 2,
        "DEMO_CHAT_MESSAGES_PER_DAY_PER_IP": 5,
        "DEMO_RETELL_TOOL_CALLS_PER_MINUTE_PER_IP": 2,
        "DEMO_RETELL_TOOL_CALLS_PER_DAY_PER_IP": 5,
        "DEMO_APPOINTMENTS_PER_DAY_PER_IP": 2,
        "DEMO_CONFIRMATION_EMAILS_PER_DAY_PER_IP": 2,
        "DEMO_GLOBAL_CHAT_MESSAGES_PER_DAY": 3,
        "DEMO_GLOBAL_APPOINTMENTS_PER_DAY": 2,
        "DEMO_GLOBAL_CONFIRMATION_EMAILS_PER_DAY": 2,
    }
    values.update(overrides)
    return Settings(_env_file=None, **values)


def make_disabled_guardrail_settings(**overrides: Any) -> Settings:
    values: dict[str, Any] = {
        "PUBLIC_DEMO_MODE": False,
        "PUBLIC_DEMO_GUARDRAILS_ENABLED": False,
        "TRUST_PROXY_HEADERS": False,
    }
    values.update(overrides)
    return Settings(_env_file=None, **values)


def make_service(
    redis_client: FakeRedisClient,
    *,
    settings: Settings | None = None,
    now: datetime | None = None,
) -> DemoGuardrailService:
    return DemoGuardrailService(
        redis_client=redis_client,
        settings=settings or make_guardrail_settings(),
        clock=FixedClock(now or FIXED_GUARD_RAIL_NOW),
    )


def create_guarded_chat_app(
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

    def override_guardrails() -> DemoGuardrailService:
        return DemoGuardrailService(
            redis_client=redis,
            settings=settings,
            clock=FixedClock(FIXED_GUARD_RAIL_NOW),
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


def create_local_chat_app(
    *,
    redis_client: FakeRedisClient,
) -> Any:
    app = create_app()
    repository = FakeConversationRepository()
    conversation_service = ConversationService(repository=repository)
    chat_service = create_chat_receptionist_service(
        conversations=conversation_service,
        scheduling=create_demo_scheduling_service_with_emily_july_availability(),
        hold_service=FakeAppointmentHoldService(),
    )
    db = FakeDatabaseSession()

    def override_guardrails() -> DemoGuardrailService:
        return DemoGuardrailService(
            redis_client=redis_client,
            settings=make_disabled_guardrail_settings(),
            clock=FixedClock(FIXED_GUARD_RAIL_NOW),
        )

    def override_chat_service() -> ChatReceptionistService:
        return chat_service

    def override_db() -> Generator[FakeDatabaseSession, None, None]:
        yield db

    app.dependency_overrides[get_demo_guardrail_service] = override_guardrails
    app.dependency_overrides[get_chat_receptionist_service] = override_chat_service
    app.dependency_overrides[get_db] = override_db
    app.dependency_overrides[get_appointment_hold_service] = (
        lambda: FakeAppointmentHoldService()
    )
    app.dependency_overrides[get_email_job_service] = (
        lambda: cast(EmailJobService, _NoopEmailJobService())
    )
    app.dependency_overrides[get_email_job_dispatch_publisher] = (
        lambda: _NoopDispatchPublisher()
    )

    return app


def create_guarded_retell_app(
    settings: Settings,
    *,
    redis_client: FakeRedisClient | None = None,
) -> tuple[Any, FakeRedisClient]:
    redis = redis_client or FakeRedisClient()
    app = create_app()

    def override_guardrails() -> DemoGuardrailService:
        return DemoGuardrailService(
            redis_client=redis,
            settings=settings,
            clock=FixedClock(FIXED_GUARD_RAIL_NOW),
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


class TrackingRedisClient(FakeRedisClient):
    def __init__(self) -> None:
        super().__init__()
        self.calls: list[str] = []

    def incr(self, key: str) -> int:
        self.calls.append(f"incr:{key}")
        return super().incr(key)

    def expire(self, key: str, seconds: int) -> bool:
        self.calls.append(f"expire:{key}")
        return super().expire(key, seconds)

    def get(self, key: str) -> str | None:
        self.calls.append(f"get:{key}")
        return super().get(key)


class FailingRedisClient(FakeRedisClient):
    def incr(self, key: str) -> int:
        raise RedisError("redis unavailable")

    def get(self, key: str) -> str | None:
        raise RedisError("redis unavailable")


class _NoopEmailJobService:
    def get_by_idempotency_key(
        self,
        *,
        job_type: EmailJobType,
        idempotency_key: str,
    ) -> EmailJob | None:
        return None

    def get_or_create_appointment_confirmation_email_job(
        self,
        payload: AppointmentConfirmationEmailJobCreate,
    ) -> AppointmentConfirmationEmailJobResult:
        email_job = self.enqueue_appointment_confirmation(payload)
        return AppointmentConfirmationEmailJobResult(email_job=email_job, created=True)

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
