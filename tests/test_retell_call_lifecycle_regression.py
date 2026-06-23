from __future__ import annotations

from collections.abc import Generator
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.adapters.retell.scheduling_tools import RetellSchedulingToolAdapter
from app.api.dependencies import (
    get_email_job_service,
    get_llm_provider,
    get_retell_call_lifecycle_service,
    get_retell_scheduling_tool_adapter,
)
from app.main import create_app
from app.services.retell_call_lifecycle import RetellCallLifecycleService
from tests.retell_webhook_support import (
    configure_retell_for_tests,
    install_fake_retell_verifier,
    make_retell_enabled_settings,
    make_secured_retell_settings,
    post_retell_tool,
)
from tests.test_retell_call_lifecycle_service import FakeVoiceCallRepository
from tests.test_retell_lifecycle_webhook import (
    LIFECYCLE_WEBHOOK_PATH,
    OCCURRED_AT,
    _lifecycle_payload,
)
from tests.test_retell_webhook_security import TrackingSchedulingService


@pytest.fixture()
def lifecycle_regression_client() -> Generator[
    tuple[TestClient, FakeVoiceCallRepository, LifecycleRegressionTrackers],
    None,
    None,
]:
    repository = FakeVoiceCallRepository()
    trackers = LifecycleRegressionTrackers()
    service = RetellCallLifecycleService(repository=repository)
    app = create_app()
    settings = make_secured_retell_settings()
    configure_retell_for_tests(app, settings=settings)
    install_fake_retell_verifier(app, accept_all=True)

    app.dependency_overrides[get_retell_call_lifecycle_service] = lambda: service
    app.dependency_overrides[get_retell_scheduling_tool_adapter] = lambda: (
        RetellSchedulingToolAdapter(trackers.scheduling)
    )
    app.dependency_overrides[get_email_job_service] = lambda: trackers.email_jobs
    app.dependency_overrides[get_llm_provider] = lambda: trackers.llm

    with TestClient(app) as client:
        yield client, repository, trackers

    app.dependency_overrides.clear()


def test_lifecycle_webhook_does_not_invoke_scheduling_email_or_llm(
    lifecycle_regression_client: tuple[
        TestClient,
        FakeVoiceCallRepository,
        LifecycleRegressionTrackers,
    ],
) -> None:
    client, repository, trackers = lifecycle_regression_client

    response = post_retell_tool(
        client,
        LIFECYCLE_WEBHOOK_PATH,
        json_body=_lifecycle_payload(),
        settings=make_secured_retell_settings(),
    )

    assert response.status_code == 200
    assert len(repository.voice_call_events) == 1
    assert trackers.scheduling.list_specialties_calls == 0
    assert trackers.email_jobs.list_calls == 0
    assert trackers.llm.complete_calls == 0


def test_local_insecure_retell_lifecycle_webhook_works_without_signature() -> None:
    repository = FakeVoiceCallRepository()
    service = RetellCallLifecycleService(repository=repository)
    app = create_app()
    settings = make_retell_enabled_settings(
        RETELL_ALLOW_INSECURE_WEBHOOKS=True,
        RETELL_WEBHOOK_VERIFICATION_ENABLED=True,
    )
    configure_retell_for_tests(app, settings=settings)
    app.dependency_overrides[get_retell_call_lifecycle_service] = lambda: service

    with TestClient(app) as client:
        response = client.post(
            LIFECYCLE_WEBHOOK_PATH,
            json={
                "call_id": "local-call-1",
                "event": "call_started",
                "occurred_at": OCCURRED_AT,
                "event_id": "evt-local",
            },
        )

    app.dependency_overrides.clear()

    assert response.status_code == 200
    assert response.json()["duplicate"] is False
    assert len(repository.voice_call_events) == 1


def test_health_route_still_works_alongside_lifecycle_routes() -> None:
    app = create_app()
    configure_retell_for_tests(app, settings=make_secured_retell_settings())

    with TestClient(app) as client:
        response = client.get("/health")

    app.dependency_overrides.clear()

    assert response.status_code == 200
    assert response.json()["status"] == "ok"


class LifecycleRegressionTrackers:
    def __init__(self) -> None:
        self.scheduling = TrackingSchedulingService()
        self.email_jobs = TrackingEmailJobService()
        self.llm = TrackingLLMProvider()


class TrackingEmailJobService:
    def __init__(self) -> None:
        self.list_calls = 0

    def list_email_jobs(self, *args: Any, **kwargs: Any) -> Any:
        self.list_calls += 1
        raise AssertionError("email job service should not be called by lifecycle ingestion")


class TrackingLLMProvider:
    def __init__(self) -> None:
        self.complete_calls = 0

    def complete(self, *args: Any, **kwargs: Any) -> Any:
        self.complete_calls += 1
        raise AssertionError("llm provider should not be called by lifecycle ingestion")

    async def acomplete(self, *args: Any, **kwargs: Any) -> Any:
        self.complete_calls += 1
        raise AssertionError("llm provider should not be called by lifecycle ingestion")
