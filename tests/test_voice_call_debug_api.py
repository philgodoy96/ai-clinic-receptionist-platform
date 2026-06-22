from __future__ import annotations

from collections.abc import Generator
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient

from app.api.dependencies import get_voice_call_inspection_service
from app.domain.voice_calls.enums import NormalizedVoiceCallEventType, VoiceCallStatus
from app.main import create_app
from app.models.voice_calls import VoiceCall, VoiceCallEvent
from app.services.voice_calls import VoiceCallInspectionService
from tests.test_retell_call_lifecycle_service import FakeVoiceCallRepository

BASE_PATH = "/api/v1/internal/voice-calls"


@pytest.fixture()
def client_and_repository() -> Generator[
    tuple[TestClient, FakeVoiceCallRepository],
    None,
    None,
]:
    repository = FakeVoiceCallRepository()
    service = VoiceCallInspectionService(repository=repository)
    app = create_app()
    app.dependency_overrides[get_voice_call_inspection_service] = lambda: service

    with TestClient(app) as test_client:
        yield test_client, repository

    app.dependency_overrides.clear()


def create_voice_call(
    *,
    provider: str = "retell",
    provider_call_id: str,
    status: VoiceCallStatus = VoiceCallStatus.IN_PROGRESS,
    created_at: datetime,
    from_number_redacted: str | None = "*******4567",
    event_metadata: dict[str, object] | None = None,
) -> VoiceCall:
    return VoiceCall(
        id=uuid4(),
        provider=provider,
        provider_call_id=provider_call_id,
        status=status,
        from_number_redacted=from_number_redacted,
        to_number_redacted=None,
        started_at=created_at,
        event_metadata=event_metadata or {"call_type": "web_call"},
        created_at=created_at,
        updated_at=created_at,
    )


def create_voice_call_event(
    *,
    voice_call_id: UUID,
    provider_call_id: str,
    event_type: str,
    occurred_at: datetime,
    provider_event_id: str,
    event_metadata: dict[str, object] | None = None,
) -> VoiceCallEvent:
    return VoiceCallEvent(
        id=uuid4(),
        voice_call_id=voice_call_id,
        provider="retell",
        provider_call_id=provider_call_id,
        provider_event_id=provider_event_id,
        event_type=event_type,
        normalized_event_type=NormalizedVoiceCallEventType.CALL_STARTED,
        occurred_at=occurred_at,
        event_metadata=event_metadata or {"duration_seconds": 12},
        idempotency_key=f"retell:{provider_call_id}:{event_type}:{provider_event_id}",
        created_at=occurred_at,
    )


def seed_repository(
    repository: FakeVoiceCallRepository,
) -> tuple[VoiceCall, VoiceCall, VoiceCallEvent]:
    older = create_voice_call(
        provider_call_id="call-older",
        created_at=datetime(2026, 6, 24, 9, 0, tzinfo=UTC),
        status=VoiceCallStatus.ENDED,
    )
    newer = create_voice_call(
        provider_call_id="call-newer",
        created_at=datetime(2026, 6, 24, 11, 0, tzinfo=UTC),
        status=VoiceCallStatus.IN_PROGRESS,
    )
    event = create_voice_call_event(
        voice_call_id=newer.id,
        provider_call_id=newer.provider_call_id,
        event_type="call_started",
        occurred_at=datetime(2026, 6, 24, 11, 0, tzinfo=UTC),
        provider_event_id="evt-1",
    )
    repository.voice_calls.extend([older, newer])
    repository.voice_call_events.append(event)
    return older, newer, event


def test_list_voice_calls(
    client_and_repository: tuple[TestClient, FakeVoiceCallRepository],
) -> None:
    client, repository = client_and_repository
    _, newer, _ = seed_repository(repository)

    response = client.get(BASE_PATH)

    assert response.status_code == 200
    body = response.json()
    assert len(body["items"]) == 2
    assert body["items"][0]["id"] == str(newer.id)
    assert body["items"][0]["provider_call_id"] == "call-newer"
    assert body["items"][0]["from_number_redacted"] == "*******4567"


def test_get_voice_call_by_id(
    client_and_repository: tuple[TestClient, FakeVoiceCallRepository],
) -> None:
    client, repository = client_and_repository
    _, newer, _ = seed_repository(repository)

    response = client.get(f"{BASE_PATH}/{newer.id}")

    assert response.status_code == 200
    body = response.json()
    assert body["id"] == str(newer.id)
    assert body["status"] == VoiceCallStatus.IN_PROGRESS.value
    assert body["event_metadata"] == {"call_type": "web_call"}


def test_list_events_for_call(
    client_and_repository: tuple[TestClient, FakeVoiceCallRepository],
) -> None:
    client, repository = client_and_repository
    _, newer, event = seed_repository(repository)
    later_event = create_voice_call_event(
        voice_call_id=newer.id,
        provider_call_id=newer.provider_call_id,
        event_type="call_ended",
        occurred_at=datetime(2026, 6, 24, 11, 5, tzinfo=UTC),
        provider_event_id="evt-2",
        event_metadata={"disconnect_reason": "user_hangup"},
    )
    repository.voice_call_events.append(later_event)

    response = client.get(f"{BASE_PATH}/{newer.id}/events")

    assert response.status_code == 200
    body = response.json()
    assert len(body["items"]) == 2
    assert body["items"][0]["id"] == str(event.id)
    assert body["items"][1]["id"] == str(later_event.id)
    assert body["items"][1]["event_metadata"] == {"disconnect_reason": "user_hangup"}


def test_list_voice_calls_filters_work(
    client_and_repository: tuple[TestClient, FakeVoiceCallRepository],
) -> None:
    client, repository = client_and_repository
    older, newer, _ = seed_repository(repository)

    status_response = client.get(
        BASE_PATH,
        params={"status": VoiceCallStatus.ENDED.value},
    )
    provider_call_response = client.get(
        BASE_PATH,
        params={"provider_call_id": newer.provider_call_id},
    )
    created_after_response = client.get(
        BASE_PATH,
        params={"created_after": (older.created_at + timedelta(minutes=30)).isoformat()},
    )

    assert status_response.status_code == 200
    assert len(status_response.json()["items"]) == 1
    assert status_response.json()["items"][0]["id"] == str(older.id)

    assert provider_call_response.status_code == 200
    assert len(provider_call_response.json()["items"]) == 1
    assert provider_call_response.json()["items"][0]["id"] == str(newer.id)

    assert created_after_response.status_code == 200
    assert len(created_after_response.json()["items"]) == 1
    assert created_after_response.json()["items"][0]["id"] == str(newer.id)


def test_unknown_voice_call_returns_404(
    client_and_repository: tuple[TestClient, FakeVoiceCallRepository],
) -> None:
    client, _repository = client_and_repository
    missing_id = uuid4()

    get_response = client.get(f"{BASE_PATH}/{missing_id}")
    events_response = client.get(f"{BASE_PATH}/{missing_id}/events")

    assert get_response.status_code == 404
    assert get_response.json()["error"]["code"] == "voice_call_not_found"
    assert events_response.status_code == 404
    assert events_response.json()["error"]["code"] == "voice_call_not_found"


def test_response_does_not_include_raw_payload_or_secrets(
    client_and_repository: tuple[TestClient, FakeVoiceCallRepository],
) -> None:
    client, repository = client_and_repository
    created_at = datetime(2026, 6, 24, 10, 0, tzinfo=UTC)
    voice_call = create_voice_call(
        provider_call_id="call-safe",
        created_at=created_at,
        from_number_redacted="*******9999",
        event_metadata={"call_type": "web_call"},
    )
    secret_transcript = "patient said secret symptoms"
    secret_number = "+15551234567"
    event = create_voice_call_event(
        voice_call_id=voice_call.id,
        provider_call_id=voice_call.provider_call_id,
        event_type="call_started",
        occurred_at=created_at,
        provider_event_id="evt-safe",
        event_metadata={
            "duration_seconds": 30,
            "transcript": secret_transcript,
            "raw_payload": {"nested": "value"},
            "api_key": "secret-key",
        },
    )
    repository.voice_calls.append(voice_call)
    repository.voice_call_events.append(event)

    call_response = client.get(f"{BASE_PATH}/{voice_call.id}")
    events_response = client.get(f"{BASE_PATH}/{voice_call.id}/events")

    assert call_response.status_code == 200
    call_body = call_response.json()
    assert set(call_body.keys()) == {
        "id",
        "provider",
        "provider_call_id",
        "conversation_id",
        "status",
        "direction",
        "from_number_redacted",
        "to_number_redacted",
        "started_at",
        "ended_at",
        "last_event_at",
        "event_metadata",
        "created_at",
        "updated_at",
    }
    assert secret_number not in str(call_body)
    assert "raw_payload" not in call_body
    assert "transcript" not in call_body

    assert events_response.status_code == 200
    events_body = events_response.json()
    assert secret_transcript not in str(events_body)
    assert secret_number not in str(events_body)
    assert "raw_payload" not in str(events_body)
    assert "api_key" not in str(events_body)
    assert events_body["items"][0]["event_metadata"] == {"duration_seconds": 30}
