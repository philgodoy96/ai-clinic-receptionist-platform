from __future__ import annotations

from collections.abc import Generator

import pytest
from fastapi.testclient import TestClient

from tests.demo_guardrail_support import (
    FakeRedisClient,
    TrackingRedisClient,
    create_guarded_retell_app,
    make_guardrail_settings,
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
