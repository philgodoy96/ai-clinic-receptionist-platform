from __future__ import annotations

from collections.abc import Generator
from typing import Any

from fastapi.testclient import TestClient

from app.api.dependencies import (
    get_demo_guardrail_service,
    get_public_demo_voice_session_service,
)
from app.core.config import Settings, get_settings
from app.db.session import get_db
from app.integrations.retell.web_call_client import RetellWebCallTimeoutError
from app.main import create_app
from app.services.clinic_time import ClinicTimeService
from app.services.clock import FixedClock
from app.services.conversations import ConversationService
from app.services.demo_guardrails import DemoGuardrailService
from app.services.public_demo_voice_session import PublicDemoVoiceSessionService
from app.services.retell_web_call import RetellWebCallService
from app.services.voice_conversation_bridge import VoiceConversationBridgeService
from tests.demo_guardrail_support import (
    FIXED_GUARD_RAIL_NOW,
    FailingRedisClient,
    FakeRedisClient,
)
from tests.test_chat_api import FakeDatabaseSession
from tests.test_conversations import FakeConversationRepository
from tests.test_retell_call_lifecycle_service import FakeVoiceCallRepository
from tests.test_retell_web_call_service import (
    TEST_ACCESS_TOKEN,
    TEST_AGENT_ID,
    TEST_API_KEY,
    TEST_CALL_ID,
    StubRetellWebCallClient,
)

VOICE_ENDPOINT = "/api/v1/demo/voice/retell-web-call"


def make_voice_settings(**overrides: Any) -> Settings:
    values: dict[str, Any] = {
        "RETELL_WEB_CALL_ENABLED": True,
        "RETELL_API_KEY": TEST_API_KEY,
        "RETELL_AGENT_ID": TEST_AGENT_ID,
        "PUBLIC_DEMO_GUARDRAILS_ENABLED": False,
    }
    values.update(overrides)
    return Settings(_env_file=None, **values)


class TrackingStubRetellWebCallClient(StubRetellWebCallClient):
    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.call_count = 0

    def create_web_call(
        self,
        *,
        payload: dict[str, Any],
        timeout_seconds: int,
    ) -> dict[str, Any]:
        self.call_count += 1
        return super().create_web_call(payload=payload, timeout_seconds=timeout_seconds)


def create_demo_voice_app(
    settings: Settings,
    *,
    redis_client: FakeRedisClient | None = None,
    retell_client: StubRetellWebCallClient | None = None,
) -> tuple[Any, StubRetellWebCallClient, FakeDatabaseSession, FakeVoiceCallRepository]:
    redis = redis_client or FakeRedisClient()
    stub = retell_client or TrackingStubRetellWebCallClient(
        response={
            "call_id": TEST_CALL_ID,
            "access_token": TEST_ACCESS_TOKEN,
        },
    )
    voice_calls = FakeVoiceCallRepository()
    conversations = FakeConversationRepository()
    conversation_service = ConversationService(repository=conversations)
    bridge = VoiceConversationBridgeService(
        voice_calls=voice_calls,
        conversations=conversations,
        conversation_service=conversation_service,
    )
    retell_service = RetellWebCallService(
        enabled=settings.retell_web_call_enabled,
        api_key=settings.retell_api_key,
        agent_id=settings.retell_agent_id,
        agent_version=settings.retell_agent_version,
        timeout_seconds=settings.retell_web_call_timeout_seconds,
        clinic_time_service=ClinicTimeService(
            clinic_name="Demo Clinic",
            timezone="America/New_York",
            business_days="monday,tuesday,wednesday,thursday,friday",
            business_hours_start="09:00",
            business_hours_end="17:00",
        ),
        client=stub,
    )
    session_service = PublicDemoVoiceSessionService(
        retell_web_call_service=retell_service,
        voice_calls=voice_calls,
        voice_conversation_bridge=bridge,
        conversations=conversation_service,
    )
    db = FakeDatabaseSession()

    app = create_app()

    def override_settings() -> Settings:
        return settings

    def override_guardrails() -> DemoGuardrailService:
        return DemoGuardrailService(
            redis_client=redis,
            settings=settings,
            clock=FixedClock(FIXED_GUARD_RAIL_NOW),
        )

    def override_db() -> Generator[FakeDatabaseSession, None, None]:
        yield db

    def override_session_service() -> PublicDemoVoiceSessionService:
        return session_service

    app.dependency_overrides[get_settings] = override_settings
    app.dependency_overrides[get_demo_guardrail_service] = override_guardrails
    app.dependency_overrides[get_db] = override_db
    app.dependency_overrides[get_public_demo_voice_session_service] = override_session_service

    return app, stub, db, voice_calls


def test_disabled_voice_rejects() -> None:
    app, stub, _, _ = create_demo_voice_app(
        make_voice_settings(RETELL_WEB_CALL_ENABLED=False),
    )

    with TestClient(app) as client:
        response = client.post(VOICE_ENDPOINT, json={})

    app.dependency_overrides.clear()

    assert response.status_code == 503
    assert response.json()["error"]["code"] == "voice_demo_disabled"
    assert stub.last_payload is None


def test_rate_limited_rejects_before_provider_call() -> None:
    settings = make_voice_settings(
        PUBLIC_DEMO_GUARDRAILS_ENABLED=True,
        DEMO_RETELL_TOOL_CALLS_PER_MINUTE_PER_IP=1,
    )
    app, stub, _, _ = create_demo_voice_app(settings)

    with TestClient(app) as client:
        first = client.post(VOICE_ENDPOINT, json={})
        second = client.post(VOICE_ENDPOINT, json={})

    app.dependency_overrides.clear()

    assert first.status_code == 200
    assert second.status_code == 429
    assert second.json()["error"]["code"] == "rate_limited"
    assert isinstance(stub, TrackingStubRetellWebCallClient)
    assert stub.call_count == 1


def test_valid_request_returns_token_and_call_id() -> None:
    app, _, db, voice_calls = create_demo_voice_app(make_voice_settings())

    with TestClient(app) as client:
        response = client.post(
            VOICE_ENDPOINT,
            json={"demo_session_id": "demo-session-1"},
        )

    app.dependency_overrides.clear()

    assert response.status_code == 200
    body = response.json()
    assert body["provider"] == "retell"
    assert body["call_id"] == TEST_CALL_ID
    assert body["access_token"] == TEST_ACCESS_TOKEN
    assert body["expires_in_seconds"] == 30
    assert body["conversation_id"] is not None
    assert db.committed is True
    assert len(voice_calls.voice_calls) == 1
    assert voice_calls.voice_calls[0].provider_call_id == TEST_CALL_ID


def test_provider_failure_returns_safe_error() -> None:
    stub = StubRetellWebCallClient(error=RetellWebCallTimeoutError("timed out"))
    app, _, db, _ = create_demo_voice_app(make_voice_settings(), retell_client=stub)

    with TestClient(app) as client:
        response = client.post(VOICE_ENDPOINT, json={})

    app.dependency_overrides.clear()

    assert response.status_code == 503
    assert response.json()["error"]["code"] == "provider_unavailable"
    assert db.rolled_back is True
    assert TEST_API_KEY not in response.text


def test_no_api_key_in_response() -> None:
    app, _, _, _ = create_demo_voice_app(make_voice_settings())

    with TestClient(app) as client:
        response = client.post(VOICE_ENDPOINT, json={})

    app.dependency_overrides.clear()

    assert response.status_code == 200
    assert TEST_API_KEY not in response.text
    assert "agent_id" not in response.text


def test_no_provider_call_when_config_disabled() -> None:
    app, stub, _, _ = create_demo_voice_app(
        make_voice_settings(RETELL_WEB_CALL_ENABLED=False),
    )

    with TestClient(app) as client:
        client.post(VOICE_ENDPOINT, json={})

    app.dependency_overrides.clear()

    assert stub.last_payload is None


def test_guardrail_store_unavailable_returns_temporary_failure() -> None:
    settings = make_voice_settings(
        PUBLIC_DEMO_GUARDRAILS_ENABLED=True,
        DEMO_RETELL_TOOL_CALLS_PER_MINUTE_PER_IP=10,
    )
    app, stub, _, _ = create_demo_voice_app(
        settings,
        redis_client=FailingRedisClient(),
    )

    with TestClient(app) as client:
        response = client.post(VOICE_ENDPOINT, json={})

    app.dependency_overrides.clear()

    assert response.status_code == 503
    assert response.json()["error"]["code"] == "temporary_failure"
    assert stub.last_payload is None
