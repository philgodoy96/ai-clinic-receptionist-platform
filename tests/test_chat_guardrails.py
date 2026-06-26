from __future__ import annotations

from collections.abc import Generator

import pytest
from fastapi.testclient import TestClient

from tests.chat_booking_flow_support import post_new_patient_booking_via_api
from tests.demo_guardrail_support import (
    FailingRedisClient,
    FakeRedisClient,
    TrackingRedisClient,
    create_guarded_chat_app,
    create_local_chat_app,
    make_chat_booking_guardrail_settings,
    make_guardrail_settings,
)


@pytest.fixture()
def guarded_chat_client() -> Generator[tuple[TestClient, FakeRedisClient], None, None]:
    app, redis_client, _ = create_guarded_chat_app(
        make_guardrail_settings(DEMO_CHAT_MESSAGES_PER_MINUTE_PER_IP=1),
    )

    with TestClient(app) as test_client:
        yield test_client, redis_client

    app.dependency_overrides.clear()


def test_local_chat_passes_without_redis_guardrail_enforcement() -> None:
    redis_client = TrackingRedisClient()
    app = create_local_chat_app(redis_client=redis_client)

    with TestClient(app) as client:
        response = client.post("/api/v1/chat/messages", json={"message": "Hi"})

    app.dependency_overrides.clear()

    assert response.status_code == 200
    assert redis_client.calls == []
    body = response.json()
    assert body["reply"]
    assert "error" not in body


def test_chat_under_limit_returns_normal_response(
    guarded_chat_client: tuple[TestClient, FakeRedisClient],
) -> None:
    client, redis_client = guarded_chat_client

    response = client.post("/api/v1/chat/messages", json={"message": "Hi"})

    assert response.status_code == 200
    body = response.json()
    assert body["reply"]
    assert "error" not in body
    assert len(redis_client.values) == 3


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


def test_redis_guardrail_failure_returns_503() -> None:
    app, _, _ = create_guarded_chat_app(
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
    app, _, _ = create_guarded_chat_app(
        make_chat_booking_guardrail_settings(),
        redis_client=redis_client,
    )

    with TestClient(app) as client:
        _book_appointment_via_chat(client)

    app.dependency_overrides.clear()

    appointment_keys = [key for key in redis_client.values if "appointment" in key]
    assert len(appointment_keys) == 2


def test_email_job_creation_increments_email_quota() -> None:
    redis_client = FakeRedisClient()
    app, _, email_jobs = create_guarded_chat_app(
        make_chat_booking_guardrail_settings(),
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
    app, _, _ = create_guarded_chat_app(
        make_chat_booking_guardrail_settings(),
        redis_client=redis_client,
    )

    with TestClient(app) as client:
        response = client.post(
            "/api/v1/chat/messages",
            json={"message": "Dr. Emily Carter on 2026-07-02"},
        )

    app.dependency_overrides.clear()

    assert response.status_code == 200
    appointment_keys = [key for key in redis_client.values if "appointment" in key]
    assert appointment_keys == []


def test_email_quota_exceeded_keeps_booking_and_skips_confirmation_email() -> None:
    redis_client = FakeRedisClient()
    app, _, email_jobs = create_guarded_chat_app(
        make_chat_booking_guardrail_settings(
            DEMO_CONFIRMATION_EMAILS_PER_DAY_PER_IP=1,
            DEMO_GLOBAL_CONFIRMATION_EMAILS_PER_DAY=1,
        ),
        redis_client=redis_client,
        track_email_jobs=True,
    )
    email_ip_key = "demo_guardrail:email:ip:testclient:day:20260621"
    redis_client.values[email_ip_key] = 1

    with TestClient(app) as client:
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
        response = post_new_patient_booking_via_api(client, conversation_id)

    app.dependency_overrides.clear()

    assert response.status_code == 200
    body = response.json()
    assert body["booking_confirmed"] is True
    assert body["confirmation_email_queued"] is False
    assert email_jobs is not None
    assert email_jobs.jobs == []


def test_guarded_chat_uses_fake_dependencies_not_real_providers(
    guarded_chat_client: tuple[TestClient, FakeRedisClient],
) -> None:
    client, redis_client = guarded_chat_client

    response = client.post("/api/v1/chat/messages", json={"message": "Hi"})

    assert response.status_code == 200
    assert isinstance(redis_client, FakeRedisClient)
    assert "error" not in response.json()


def _book_appointment_via_chat(client: TestClient) -> str:
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
    response = post_new_patient_booking_via_api(client, conversation_id)
    assert response.status_code == 200
    body = response.json()
    assert body["booking_confirmed"] is True
    return str(body["conversation_id"])
