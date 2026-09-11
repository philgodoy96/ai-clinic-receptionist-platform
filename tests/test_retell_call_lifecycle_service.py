from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID, uuid4

import pytest
from sqlalchemy.exc import IntegrityError

from app.domain.voice_calls.enums import NormalizedVoiceCallEventType, VoiceCallStatus
from app.models.voice_calls import VoiceCall, VoiceCallEvent
from app.services.retell_call_lifecycle import (
    MissingProviderCallIdError,
    RetellCallLifecycleService,
    RetellLifecyclePayload,
)
from app.services.voice_call_pagination import VoiceCallCursor, VoiceCallEventCursor


def _payload(
    *,
    provider_call_id: str = "call-123",
    event_type: str = "call_started",
    occurred_at: datetime | None = None,
    provider_event_id: str | None = "evt-1",
    sequence_number: int | None = None,
    direction: str | None = "inbound",
    from_number: str | None = "+15551234567",
    to_number: str | None = "+15559876543",
    safe_metadata: dict[str, object] | None = None,
) -> RetellLifecyclePayload:
    return RetellLifecyclePayload(
        provider_call_id=provider_call_id,
        event_type=event_type,
        occurred_at=occurred_at or datetime(2026, 6, 24, 10, 0, tzinfo=UTC),
        provider_event_id=provider_event_id,
        sequence_number=sequence_number,
        direction=direction,
        from_number=from_number,
        to_number=to_number,
        safe_metadata=safe_metadata or {},
    )


def test_call_start_event_creates_voice_call_and_event() -> None:
    repository = FakeVoiceCallRepository()
    service = RetellCallLifecycleService(repository=repository)

    result = service.ingest_event(_payload())

    assert result.created_call is True
    assert result.created_event is True
    assert len(repository.voice_calls) == 1
    assert len(repository.voice_call_events) == 1
    assert result.voice_call.provider == "retell"
    assert result.voice_call.provider_call_id == "call-123"
    assert result.voice_call.status == VoiceCallStatus.IN_PROGRESS
    assert result.voice_call.started_at == datetime(2026, 6, 24, 10, 0, tzinfo=UTC)
    assert result.voice_call.from_number_redacted == "*******4567"
    assert result.voice_call.to_number_redacted == "*******6543"
    assert result.voice_call_event.normalized_event_type == (
        NormalizedVoiceCallEventType.CALL_STARTED
    )


def test_duplicate_event_is_idempotent() -> None:
    repository = FakeVoiceCallRepository()
    service = RetellCallLifecycleService(repository=repository)
    payload = _payload()

    first = service.ingest_event(payload)
    second = service.ingest_event(payload)

    assert first.created_call is True
    assert first.created_event is True
    assert second.created_call is False
    assert second.created_event is False
    assert second.voice_call.id == first.voice_call.id
    assert second.voice_call_event.id == first.voice_call_event.id
    assert len(repository.voice_calls) == 1
    assert len(repository.voice_call_events) == 1


def test_call_ended_updates_status_and_ended_at() -> None:
    repository = FakeVoiceCallRepository()
    service = RetellCallLifecycleService(repository=repository)
    started_at = datetime(2026, 6, 24, 10, 0, tzinfo=UTC)
    ended_at = started_at + timedelta(minutes=5)

    service.ingest_event(
        _payload(
            event_type="call_started",
            occurred_at=started_at,
            provider_event_id="evt-start",
        ),
    )
    result = service.ingest_event(
        _payload(
            event_type="call_ended",
            occurred_at=ended_at,
            provider_event_id="evt-end",
        ),
    )

    assert result.voice_call.status == VoiceCallStatus.ENDED
    assert result.voice_call.ended_at == ended_at
    assert result.voice_call.started_at == started_at


def test_update_event_updates_last_event_at() -> None:
    repository = FakeVoiceCallRepository()
    service = RetellCallLifecycleService(repository=repository)
    first_at = datetime(2026, 6, 24, 10, 0, tzinfo=UTC)
    second_at = first_at + timedelta(minutes=2)

    service.ingest_event(
        _payload(
            event_type="call_started",
            occurred_at=first_at,
            provider_event_id="evt-1",
        ),
    )
    result = service.ingest_event(
        _payload(
            event_type="call_updated",
            occurred_at=second_at,
            provider_event_id="evt-2",
        ),
    )

    assert result.voice_call.last_event_at == second_at
    assert len(repository.voice_call_events) == 2


def test_failed_event_sets_status_failed() -> None:
    repository = FakeVoiceCallRepository()
    service = RetellCallLifecycleService(repository=repository)
    failed_at = datetime(2026, 6, 24, 10, 30, tzinfo=UTC)

    service.ingest_event(
        _payload(
            event_type="call_started",
            occurred_at=datetime(2026, 6, 24, 10, 0, tzinfo=UTC),
            provider_event_id="evt-start",
        ),
    )
    result = service.ingest_event(
        _payload(
            event_type="call_failed",
            occurred_at=failed_at,
            provider_event_id="evt-failed",
        ),
    )

    assert result.voice_call.status == VoiceCallStatus.FAILED
    assert result.voice_call.ended_at == failed_at
    assert result.voice_call_event.normalized_event_type == (
        NormalizedVoiceCallEventType.CALL_FAILED
    )


def test_web_call_without_phone_numbers_leaves_redacted_fields_null() -> None:
    repository = FakeVoiceCallRepository()
    service = RetellCallLifecycleService(repository=repository)

    result = service.ingest_event(
        _payload(
            from_number=None,
            to_number=None,
            direction="web",
        ),
    )

    assert result.voice_call.from_number_redacted is None
    assert result.voice_call.to_number_redacted is None
    assert result.voice_call.direction == "web"


def test_out_of_order_older_event_does_not_downgrade_ended_status() -> None:
    repository = FakeVoiceCallRepository()
    service = RetellCallLifecycleService(repository=repository)
    started_at = datetime(2026, 6, 24, 10, 0, tzinfo=UTC)
    ended_at = started_at + timedelta(minutes=5)

    service.ingest_event(
        _payload(
            event_type="call_ended",
            occurred_at=ended_at,
            provider_event_id="evt-end",
        ),
    )
    result = service.ingest_event(
        _payload(
            event_type="call_started",
            occurred_at=started_at,
            provider_event_id="evt-start",
        ),
    )

    assert result.voice_call.status == VoiceCallStatus.ENDED
    assert result.voice_call.ended_at == ended_at
    assert result.voice_call.started_at == started_at


def test_unknown_event_type_is_persisted_safely() -> None:
    repository = FakeVoiceCallRepository()
    service = RetellCallLifecycleService(repository=repository)

    result = service.ingest_event(
        _payload(
            event_type="provider_specific_signal",
            provider_event_id="evt-unknown",
        ),
    )

    assert result.created_event is True
    assert result.voice_call_event.event_type == "provider_specific_signal"
    assert result.voice_call_event.normalized_event_type == (NormalizedVoiceCallEventType.UNKNOWN)
    assert result.voice_call.status == VoiceCallStatus.CREATED


def test_missing_provider_call_id_is_rejected() -> None:
    repository = FakeVoiceCallRepository()
    service = RetellCallLifecycleService(repository=repository)

    with pytest.raises(MissingProviderCallIdError):
        service.ingest_event(_payload(provider_call_id="   "))


def test_phone_numbers_are_redacted() -> None:
    repository = FakeVoiceCallRepository()
    service = RetellCallLifecycleService(repository=repository)

    result = service.ingest_event(
        _payload(
            from_number="+15551234567",
            to_number="+15559876543",
        ),
    )

    assert result.voice_call.from_number_redacted == "*******4567"
    assert result.voice_call.to_number_redacted == "*******6543"


def test_raw_payload_is_not_stored() -> None:
    repository = FakeVoiceCallRepository()
    service = RetellCallLifecycleService(repository=repository)

    result = service.ingest_event(
        _payload(
            safe_metadata={
                "duration_seconds": 42,
                "disconnect_reason": "user_hangup",
                "transcript": "secret conversation",
                "recording_url": "https://example.test/recording",
                "raw_payload": {"call": "full"},
                "api_key": "secret",
            },
        ),
    )

    assert result.voice_call_event.event_metadata == {
        "duration_seconds": 42,
        "disconnect_reason": "user_hangup",
    }
    assert "transcript" not in result.voice_call_event.event_metadata
    assert "recording_url" not in result.voice_call_event.event_metadata
    assert "raw_payload" not in result.voice_call_event.event_metadata
    assert "api_key" not in result.voice_call_event.event_metadata


def test_duplicate_event_race_returns_existing_event() -> None:
    repository = RacingVoiceCallRepository()
    service = RetellCallLifecycleService(repository=repository)
    payload = _payload()

    result = service.ingest_event(payload)

    assert result.created_event is False
    assert len(repository.voice_call_events) == 1
    assert result.voice_call_event.idempotency_key.startswith("retell:call-123:call_started:")


class FakeVoiceCallRepository:
    def __init__(self) -> None:
        self.voice_calls: list[VoiceCall] = []
        self.voice_call_events: list[VoiceCallEvent] = []

    def get_by_id(self, voice_call_id: UUID) -> VoiceCall | None:
        return next(
            (voice_call for voice_call in self.voice_calls if voice_call.id == voice_call_id),
            None,
        )

    def get_by_provider_call_id(
        self,
        *,
        provider: str,
        provider_call_id: str,
    ) -> VoiceCall | None:
        return next(
            (
                voice_call
                for voice_call in self.voice_calls
                if voice_call.provider == provider
                and voice_call.provider_call_id == provider_call_id
            ),
            None,
        )

    def create_voice_call(self, voice_call: VoiceCall) -> VoiceCall:
        if voice_call.id is None:
            voice_call.id = uuid4()

        existing = self.get_by_provider_call_id(
            provider=voice_call.provider,
            provider_call_id=voice_call.provider_call_id,
        )
        if existing is not None:
            raise IntegrityError(
                "duplicate provider call id",
                {},
                Exception("duplicate provider call id"),
            )

        self.voice_calls.append(voice_call)
        return voice_call

    def get_or_create_voice_call(
        self,
        *,
        provider: str,
        provider_call_id: str,
    ) -> tuple[VoiceCall, bool]:
        existing = self.get_by_provider_call_id(
            provider=provider,
            provider_call_id=provider_call_id,
        )
        if existing is not None:
            return existing, False

        return self.create_voice_call(
            VoiceCall(
                provider=provider,
                provider_call_id=provider_call_id,
                status=VoiceCallStatus.CREATED,
            ),
        ), True

    def update_voice_call(self, voice_call: VoiceCall) -> VoiceCall:
        return voice_call

    def create_voice_call_event(self, voice_call_event: VoiceCallEvent) -> VoiceCallEvent:
        if voice_call_event.id is None:
            voice_call_event.id = uuid4()

        existing = self.get_event_by_idempotency_key(
            idempotency_key=voice_call_event.idempotency_key,
        )
        if existing is not None:
            raise IntegrityError(
                "duplicate idempotency key",
                {},
                Exception("duplicate idempotency key"),
            )

        self.voice_call_events.append(voice_call_event)
        return voice_call_event

    def get_event_by_idempotency_key(
        self,
        *,
        idempotency_key: str,
    ) -> VoiceCallEvent | None:
        return next(
            (
                voice_call_event
                for voice_call_event in self.voice_call_events
                if voice_call_event.idempotency_key == idempotency_key
            ),
            None,
        )

    def list_voice_calls(
        self,
        *,
        limit: int,
        cursor: VoiceCallCursor | None = None,
        status: VoiceCallStatus | None = None,
        provider: str | None = None,
        provider_call_id: str | None = None,
        created_after: datetime | None = None,
    ) -> Sequence[VoiceCall]:
        voice_calls = sorted(
            self.voice_calls,
            key=lambda voice_call: (voice_call.created_at, voice_call.id),
            reverse=True,
        )

        if status is not None:
            voice_calls = [voice_call for voice_call in voice_calls if voice_call.status == status]
        if provider is not None:
            voice_calls = [
                voice_call for voice_call in voice_calls if voice_call.provider == provider
            ]
        if provider_call_id is not None:
            voice_calls = [
                voice_call
                for voice_call in voice_calls
                if voice_call.provider_call_id == provider_call_id
            ]
        if created_after is not None:
            voice_calls = [
                voice_call for voice_call in voice_calls if voice_call.created_at >= created_after
            ]
        if cursor is not None:
            voice_calls = [
                voice_call
                for voice_call in voice_calls
                if (
                    voice_call.created_at < cursor.created_at
                    or (voice_call.created_at == cursor.created_at and voice_call.id < cursor.id)
                )
            ]

        return voice_calls[:limit]

    def list_events_for_call(
        self,
        *,
        voice_call_id: UUID,
        limit: int,
        cursor: VoiceCallEventCursor | None = None,
    ) -> Sequence[VoiceCallEvent]:
        events = sorted(
            [
                voice_call_event
                for voice_call_event in self.voice_call_events
                if voice_call_event.voice_call_id == voice_call_id
            ],
            key=lambda voice_call_event: (
                voice_call_event.occurred_at,
                voice_call_event.id,
            ),
        )

        if cursor is not None:
            events = [
                voice_call_event
                for voice_call_event in events
                if (
                    voice_call_event.occurred_at > cursor.occurred_at
                    or (
                        voice_call_event.occurred_at == cursor.occurred_at
                        and voice_call_event.id > cursor.id
                    )
                )
            ]

        return events[:limit]

    def get_tool_call_outcome_by_idempotency_key(
        self,
        *,
        idempotency_key: str,
    ) -> dict[str, Any] | None:
        event = self.get_event_by_idempotency_key(idempotency_key=idempotency_key)

        if event is None:
            return None

        from app.domain.voice_tool_execution import (
            VoiceToolExecutionStatus,
            read_tool_execution_outcome,
            read_tool_execution_status,
        )

        status = read_tool_execution_status(event.event_metadata)
        if status is VoiceToolExecutionStatus.IN_PROGRESS:
            return None
        if status is VoiceToolExecutionStatus.FAILED:
            return None

        return read_tool_execution_outcome(event.event_metadata)

    def claim_tool_call_execution(
        self,
        *,
        voice_call_id: UUID,
        provider: str,
        provider_call_id: str,
        event_type: str,
        tool_call_id: str,
        idempotency_key: str,
        occurred_at: datetime,
        stale_reclaim_policy: Any = None,
    ) -> Any:
        from app.domain.voice_tool_execution import (
            ToolExecutionClaimResult,
            VoiceToolExecutionStatus,
            build_in_progress_execution_metadata,
            read_tool_execution_outcome,
            read_tool_execution_status,
        )

        _ = stale_reclaim_policy

        existing = self.get_event_by_idempotency_key(idempotency_key=idempotency_key)
        if existing is not None:
            status = read_tool_execution_status(existing.event_metadata)
            outcome = read_tool_execution_outcome(existing.event_metadata)
            if status is VoiceToolExecutionStatus.SUCCEEDED or (
                status is None and outcome is not None
            ):
                return ToolExecutionClaimResult(
                    owned=False,
                    status=VoiceToolExecutionStatus.SUCCEEDED,
                    outcome=outcome,
                )
            if status is VoiceToolExecutionStatus.FAILED:
                existing.event_metadata = build_in_progress_execution_metadata()
                return ToolExecutionClaimResult(
                    owned=True,
                    status=VoiceToolExecutionStatus.IN_PROGRESS,
                    outcome=None,
                )
            return ToolExecutionClaimResult(
                owned=False,
                status=VoiceToolExecutionStatus.IN_PROGRESS,
                outcome=None,
            )

        self.create_voice_call_event(
            VoiceCallEvent(
                voice_call_id=voice_call_id,
                provider=provider,
                provider_call_id=provider_call_id,
                provider_event_id=tool_call_id,
                event_type=event_type,
                occurred_at=occurred_at,
                event_metadata=build_in_progress_execution_metadata(),
                idempotency_key=idempotency_key,
            ),
        )
        return ToolExecutionClaimResult(
            owned=True,
            status=VoiceToolExecutionStatus.IN_PROGRESS,
            outcome=None,
        )

    def complete_tool_call_execution(
        self,
        *,
        idempotency_key: str,
        outcome: dict[str, Any],
    ) -> bool:
        from app.domain.voice_tool_execution import build_succeeded_execution_metadata

        event = self.get_event_by_idempotency_key(idempotency_key=idempotency_key)
        if event is None:
            return False
        event.event_metadata = build_succeeded_execution_metadata(outcome)
        return True

    def fail_tool_call_execution(
        self,
        *,
        idempotency_key: str,
        error_code: str | None = None,
    ) -> bool:
        from app.domain.voice_tool_execution import (
            VoiceToolExecutionStatus,
            build_failed_execution_metadata,
            read_tool_execution_status,
        )

        event = self.get_event_by_idempotency_key(idempotency_key=idempotency_key)
        if event is None:
            return False
        if read_tool_execution_status(event.event_metadata) is VoiceToolExecutionStatus.SUCCEEDED:
            return False
        event.event_metadata = build_failed_execution_metadata(error_code=error_code)
        return True

    def record_tool_call_outcome(
        self,
        *,
        voice_call_id: UUID,
        provider: str,
        provider_call_id: str,
        event_type: str,
        tool_call_id: str,
        idempotency_key: str,
        outcome: dict[str, Any],
        occurred_at: datetime,
    ) -> bool:
        from app.domain.voice_tool_execution import build_succeeded_execution_metadata

        if self.get_event_by_idempotency_key(idempotency_key=idempotency_key) is not None:
            return self.complete_tool_call_execution(
                idempotency_key=idempotency_key,
                outcome=outcome,
            )

        self.create_voice_call_event(
            VoiceCallEvent(
                voice_call_id=voice_call_id,
                provider=provider,
                provider_call_id=provider_call_id,
                provider_event_id=tool_call_id,
                event_type=event_type,
                occurred_at=occurred_at,
                event_metadata=build_succeeded_execution_metadata(outcome),
                idempotency_key=idempotency_key,
            ),
        )

        return True


class RacingVoiceCallRepository(FakeVoiceCallRepository):
    def create_voice_call_event(self, voice_call_event: VoiceCallEvent) -> VoiceCallEvent:
        if voice_call_event.id is None:
            voice_call_event.id = uuid4()

        raced_event = VoiceCallEvent(
            id=uuid4(),
            voice_call_id=voice_call_event.voice_call_id,
            provider=voice_call_event.provider,
            provider_call_id=voice_call_event.provider_call_id,
            provider_event_id=voice_call_event.provider_event_id,
            event_type=voice_call_event.event_type,
            normalized_event_type=voice_call_event.normalized_event_type,
            occurred_at=voice_call_event.occurred_at,
            sequence_number=voice_call_event.sequence_number,
            event_metadata=voice_call_event.event_metadata,
            idempotency_key=voice_call_event.idempotency_key,
        )
        self.voice_call_events.append(raced_event)
        raise IntegrityError(
            "duplicate idempotency key",
            {},
            Exception("duplicate idempotency key"),
        )
