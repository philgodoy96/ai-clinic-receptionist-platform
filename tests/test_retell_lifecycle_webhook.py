from __future__ import annotations

from fastapi.testclient import TestClient

from app.api.dependencies import get_retell_call_lifecycle_service
from app.domain.voice_calls.enums import NormalizedVoiceCallEventType
from app.main import create_app
from app.services.retell_call_lifecycle import (
    RetellCallLifecycleService,
    RetellLifecycleIngestionResult,
    RetellLifecyclePayload,
)
from tests.retell_webhook_support import (
    configure_retell_for_tests,
    install_fake_retell_verifier,
    make_secured_retell_settings,
    post_retell_tool,
    retell_request_headers,
)
from tests.test_retell_call_lifecycle_service import FakeVoiceCallRepository

LIFECYCLE_WEBHOOK_PATH = "/api/v1/retell/webhooks/lifecycle"
OCCURRED_AT = "2026-06-24T10:00:00+00:00"


def _lifecycle_payload(
    *,
    call_id: str = "call-123",
    event: str = "call_started",
    provider_event_id: str = "evt-1",
    extra: dict[str, object] | None = None,
) -> dict[str, object]:
    payload: dict[str, object] = {
        "call_id": call_id,
        "event": event,
        "occurred_at": OCCURRED_AT,
        "event_id": provider_event_id,
    }
    if extra is not None:
        payload.update(extra)

    return payload


def test_valid_verified_lifecycle_event_is_persisted() -> None:
    repository = FakeVoiceCallRepository()
    service = RetellCallLifecycleService(repository=repository)
    app = create_app()
    settings = make_secured_retell_settings()
    configure_retell_for_tests(app, settings=settings)
    install_fake_retell_verifier(app, accept_all=True)
    app.dependency_overrides[get_retell_call_lifecycle_service] = lambda: service

    with TestClient(app) as client:
        response = post_retell_tool(
            client,
            LIFECYCLE_WEBHOOK_PATH,
            json_body=_lifecycle_payload(),
            settings=settings,
        )

    app.dependency_overrides.clear()

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "accepted"
    assert body["duplicate"] is False
    assert len(repository.voice_calls) == 1
    assert len(repository.voice_call_events) == 1
    assert body["voice_call_id"] == str(repository.voice_calls[0].id)
    assert body["event_id"] == str(repository.voice_call_events[0].id)


def test_duplicate_verified_lifecycle_event_is_idempotent() -> None:
    repository = FakeVoiceCallRepository()
    service = RetellCallLifecycleService(repository=repository)
    app = create_app()
    settings = make_secured_retell_settings()
    configure_retell_for_tests(app, settings=settings)
    install_fake_retell_verifier(app, accept_all=True)
    app.dependency_overrides[get_retell_call_lifecycle_service] = lambda: service
    payload = _lifecycle_payload()

    with TestClient(app) as client:
        first = post_retell_tool(
            client,
            LIFECYCLE_WEBHOOK_PATH,
            json_body=payload,
            settings=settings,
        )
        second = post_retell_tool(
            client,
            LIFECYCLE_WEBHOOK_PATH,
            json_body=payload,
            settings=settings,
        )

    app.dependency_overrides.clear()

    assert first.status_code == 200
    assert second.status_code == 200
    assert first.json()["duplicate"] is False
    assert second.json()["duplicate"] is True
    assert second.json()["voice_call_id"] == first.json()["voice_call_id"]
    assert second.json()["event_id"] == first.json()["event_id"]
    assert len(repository.voice_call_events) == 1


def test_invalid_signature_does_not_call_lifecycle_service() -> None:
    repository = FakeVoiceCallRepository()
    tracking_service = TrackingRetellCallLifecycleService(
        repository=repository,
    )
    app = create_app()
    settings = make_secured_retell_settings()
    configure_retell_for_tests(app, settings=settings)
    install_fake_retell_verifier(app)
    app.dependency_overrides[get_retell_call_lifecycle_service] = lambda: tracking_service

    with TestClient(app) as client:
        response = post_retell_tool(
            client,
            LIFECYCLE_WEBHOOK_PATH,
            json_body=_lifecycle_payload(),
            settings=settings,
            headers=retell_request_headers(signature="v=1,d=bad"),
        )

    app.dependency_overrides.clear()

    assert response.status_code == 401
    assert tracking_service.ingest_calls == 0
    assert len(repository.voice_call_events) == 0


def test_malformed_payload_returns_400() -> None:
    repository = FakeVoiceCallRepository()
    service = RetellCallLifecycleService(repository=repository)
    app = create_app()
    settings = make_secured_retell_settings()
    configure_retell_for_tests(app, settings=settings)
    install_fake_retell_verifier(app, accept_all=True)
    app.dependency_overrides[get_retell_call_lifecycle_service] = lambda: service

    with TestClient(app) as client:
        response = post_retell_tool(
            client,
            LIFECYCLE_WEBHOOK_PATH,
            json_body={"event": "call_started"},
            settings=settings,
        )

    app.dependency_overrides.clear()

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "invalid_retell_payload"
    assert len(repository.voice_call_events) == 0


def test_unknown_event_type_is_persisted_as_unknown() -> None:
    repository = FakeVoiceCallRepository()
    service = RetellCallLifecycleService(repository=repository)
    app = create_app()
    settings = make_secured_retell_settings()
    configure_retell_for_tests(app, settings=settings)
    install_fake_retell_verifier(app, accept_all=True)
    app.dependency_overrides[get_retell_call_lifecycle_service] = lambda: service

    with TestClient(app) as client:
        response = post_retell_tool(
            client,
            LIFECYCLE_WEBHOOK_PATH,
            json_body=_lifecycle_payload(
                event="provider_specific_signal",
                provider_event_id="evt-unknown",
            ),
            settings=settings,
        )

    app.dependency_overrides.clear()

    assert response.status_code == 200
    assert len(repository.voice_call_events) == 1
    assert repository.voice_call_events[0].normalized_event_type == (
        NormalizedVoiceCallEventType.UNKNOWN
    )


def test_response_does_not_expose_raw_payload() -> None:
    repository = FakeVoiceCallRepository()
    service = RetellCallLifecycleService(repository=repository)
    app = create_app()
    settings = make_secured_retell_settings()
    configure_retell_for_tests(app, settings=settings)
    install_fake_retell_verifier(app, accept_all=True)
    app.dependency_overrides[get_retell_call_lifecycle_service] = lambda: service
    secret_transcript = "patient said secret symptoms"
    secret_number = "+15551234567"

    with TestClient(app) as client:
        response = post_retell_tool(
            client,
            LIFECYCLE_WEBHOOK_PATH,
            json_body=_lifecycle_payload(
                extra={
                    "from_number": secret_number,
                    "transcript": secret_transcript,
                    "raw_payload": {"nested": "value"},
                },
            ),
            settings=settings,
        )

    app.dependency_overrides.clear()

    assert response.status_code == 200
    body = response.json()
    assert set(body.keys()) == {"status", "voice_call_id", "event_id", "duplicate"}
    assert secret_transcript not in str(body)
    assert secret_number not in str(body)
    assert "raw_payload" not in str(body)
    assert repository.voice_calls[0].from_number_redacted == "*******4567"


class TrackingRetellCallLifecycleService:
    def __init__(self, *, repository: FakeVoiceCallRepository) -> None:
        self._service = RetellCallLifecycleService(repository=repository)
        self.ingest_calls = 0

    def ingest_event(self, payload: RetellLifecyclePayload) -> RetellLifecycleIngestionResult:
        self.ingest_calls += 1
        return self._service.ingest_event(payload)
