from __future__ import annotations

import threading
from collections.abc import Generator
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

import app.db.base  # noqa: F401
from app.db.base_class import Base
from app.domain.retell_tools import (
    build_retell_tool_call_idempotency_key,
    build_succeeded_tool_call_response,
    serialize_tool_call_outcome,
)
from app.domain.voice_booking import VoiceBookingTemporaryFailureError
from app.domain.voice_booking_enums import VoiceBookingAttemptStatus
from app.domain.voice_tool_execution import (
    AMBIGUOUS_DUAL_WRITE_RECOVERY,
    TOOL_EXECUTION_AMBIGUOUS_RECOVERY_ERROR_CODE,
    TOOL_EXECUTION_IN_PROGRESS_ERROR_CODE,
    ToolExecutionStaleReclaimPolicy,
    VoiceToolExecutionStatus,
    build_in_progress_execution_metadata,
    read_tool_execution_recovery,
)
from app.models.voice_booking_attempt import VoiceBookingAttempt
from app.models.voice_calls import VoiceCall
from app.repositories.sqlalchemy.voice_booking_attempts import (
    SQLAlchemyVoiceBookingAttemptRepository,
)
from app.repositories.sqlalchemy.voice_calls import SQLAlchemyVoiceCallRepository
from app.schemas.retell_tools import RetellToolCallRequest
from app.services.retell_tool_adapter import RetellToolCallingAdapter
from tests.test_voice_booking_confirmation_service import (
    _active_hold_id,
    _build_request,
    create_voice_booking_confirmation_context,
)


@pytest.fixture()
def shared_sqlite_path(tmp_path: Any) -> Generator[str, None, None]:
    db_path = tmp_path / "voice_tool_idempotency.sqlite"
    engine = create_engine(
        f"sqlite+pysqlite:///{db_path}",
        connect_args={"check_same_thread": False, "timeout": 30},
    )
    with engine.connect() as connection:
        connection.exec_driver_sql("PRAGMA foreign_keys=ON")
        connection.commit()
    Base.metadata.create_all(bind=engine)
    engine.dispose()
    yield str(db_path)
    if db_path.exists():
        db_path.unlink()


def _engine_for_path(db_path: str) -> Any:
    return create_engine(
        f"sqlite+pysqlite:///{db_path}",
        connect_args={"check_same_thread": False, "timeout": 30},
    )


def _session_factory(engine: Any) -> sessionmaker[Session]:
    return sessionmaker(
        bind=engine,
        autoflush=False,
        autocommit=False,
        expire_on_commit=False,
    )


def _seed_voice_call(session: Session) -> VoiceCall:
    voice_call = VoiceCall(
        id=uuid4(),
        provider="retell",
        provider_call_id="call-concurrent-1",
    )
    session.add(voice_call)
    session.commit()
    return voice_call


def test_claim_repository_only_one_owner_under_concurrent_race(
    shared_sqlite_path: str,
) -> None:
    setup_engine = _engine_for_path(shared_sqlite_path)
    SessionLocal = _session_factory(setup_engine)
    with SessionLocal() as setup:
        voice_call = _seed_voice_call(setup)
        voice_call_id = voice_call.id
    setup_engine.dispose()

    idempotency_key = build_retell_tool_call_idempotency_key(
        provider="retell",
        provider_call_id="call-concurrent-1",
        tool_name="book_appointment",
        tool_call_id="tool-concurrent-1",
    )
    barrier = threading.Barrier(2)
    results: list[bool] = []
    errors: list[BaseException] = []
    lock = threading.Lock()

    def worker() -> None:
        engine = _engine_for_path(shared_sqlite_path)
        SessionLocalWorker = _session_factory(engine)
        try:
            with SessionLocalWorker() as session:
                repository = SQLAlchemyVoiceCallRepository(session)
                barrier.wait(timeout=5)
                claim = repository.claim_tool_call_execution(
                    voice_call_id=voice_call_id,
                    provider="retell",
                    provider_call_id="call-concurrent-1",
                    event_type="retell_tool:book_appointment",
                    tool_call_id="tool-concurrent-1",
                    idempotency_key=idempotency_key,
                    occurred_at=datetime.now(UTC),
                )
                session.commit()
                with lock:
                    results.append(claim.owned)
        except BaseException as exc:  # noqa: BLE001 - capture for assertion
            with lock:
                errors.append(exc)
        finally:
            engine.dispose()

    threads = [threading.Thread(target=worker) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=10)

    assert errors == []
    assert sorted(results) == [False, True]

    verify_engine = _engine_for_path(shared_sqlite_path)
    with _session_factory(verify_engine)() as session:
        repository = SQLAlchemyVoiceCallRepository(session)
        stored = repository.get_event_by_idempotency_key(idempotency_key=idempotency_key)
        assert stored is not None
        assert stored.event_metadata["execution_status"] == "in_progress"
    verify_engine.dispose()


def test_complete_outcome_reused_and_failed_claim_allows_retry(
    shared_sqlite_path: str,
) -> None:
    engine = _engine_for_path(shared_sqlite_path)
    SessionLocal = _session_factory(engine)
    with SessionLocal() as session:
        voice_call = _seed_voice_call(session)
        repository = SQLAlchemyVoiceCallRepository(session)
        key = build_retell_tool_call_idempotency_key(
            provider="retell",
            provider_call_id="call-concurrent-1",
            tool_name="cancel_appointment",
            tool_call_id="tool-cancel-1",
        )
        claim = repository.claim_tool_call_execution(
            voice_call_id=voice_call.id,
            provider="retell",
            provider_call_id="call-concurrent-1",
            event_type="retell_tool:cancel_appointment",
            tool_call_id="tool-cancel-1",
            idempotency_key=key,
            occurred_at=datetime.now(UTC),
        )
        assert claim.owned is True
        session.commit()

        repository.fail_tool_call_execution(idempotency_key=key, error_code="boom")
        session.commit()

        retry_claim = repository.claim_tool_call_execution(
            voice_call_id=voice_call.id,
            provider="retell",
            provider_call_id="call-concurrent-1",
            event_type="retell_tool:cancel_appointment",
            tool_call_id="tool-cancel-1",
            idempotency_key=key,
            occurred_at=datetime.now(UTC),
        )
        assert retry_claim.owned is True
        session.commit()

        outcome = serialize_tool_call_outcome(
            build_succeeded_tool_call_response(
                tool_name="cancel_appointment",
                tool_call_id="tool-cancel-1",
                result={"ok": True},
            ),
        )
        repository.complete_tool_call_execution(idempotency_key=key, outcome=outcome)
        session.commit()

        assert repository.get_tool_call_outcome_by_idempotency_key(idempotency_key=key) == outcome

        later = repository.claim_tool_call_execution(
            voice_call_id=voice_call.id,
            provider="retell",
            provider_call_id="call-concurrent-1",
            event_type="retell_tool:cancel_appointment",
            tool_call_id="tool-cancel-1",
            idempotency_key=key,
            occurred_at=datetime.now(UTC),
        )
        assert later.owned is False
        assert later.status is VoiceToolExecutionStatus.SUCCEEDED
    engine.dispose()


def test_different_tool_execution_identities_claim_independently(
    shared_sqlite_path: str,
) -> None:
    engine = _engine_for_path(shared_sqlite_path)
    SessionLocal = _session_factory(engine)
    with SessionLocal() as session:
        voice_call = _seed_voice_call(session)
        repository = SQLAlchemyVoiceCallRepository(session)
        first_key = build_retell_tool_call_idempotency_key(
            provider="retell",
            provider_call_id="call-concurrent-1",
            tool_name="hold_appointment_slot",
            tool_call_id="tool-a",
        )
        second_key = build_retell_tool_call_idempotency_key(
            provider="retell",
            provider_call_id="call-concurrent-1",
            tool_name="hold_appointment_slot",
            tool_call_id="tool-b",
        )
        first = repository.claim_tool_call_execution(
            voice_call_id=voice_call.id,
            provider="retell",
            provider_call_id="call-concurrent-1",
            event_type="retell_tool:hold_appointment_slot",
            tool_call_id="tool-a",
            idempotency_key=first_key,
            occurred_at=datetime.now(UTC),
        )
        second = repository.claim_tool_call_execution(
            voice_call_id=voice_call.id,
            provider="retell",
            provider_call_id="call-concurrent-1",
            event_type="retell_tool:hold_appointment_slot",
            tool_call_id="tool-b",
            idempotency_key=second_key,
            occurred_at=datetime.now(UTC),
        )
        session.commit()
        assert first.owned is True
        assert second.owned is True
    engine.dispose()


def test_adapter_in_progress_duplicate_does_not_execute_side_effect() -> None:
    from tests.test_retell_tool_adapter import TrackingVoiceCallRepository

    voice_calls = TrackingVoiceCallRepository()

    class CountingHoldService:
        ttl_seconds = 300

        def create_hold(self, **kwargs: Any) -> Any:
            raise AssertionError("side effect must not run for in-progress duplicate")

        def release_hold_by_id(self, **kwargs: Any) -> None:
            raise AssertionError("unexpected release")

    class StubScheduling:
        def get_available_slot_for_hold(self, availability_slot_id: Any) -> Any:
            raise AssertionError("unexpected scheduling call")

        def list_specialties(self) -> list[Any]:
            return []

        def list_doctors(self, specialty_id: Any = None) -> list[Any]:
            return []

        def check_availability(self, **kwargs: Any) -> list[Any]:
            return []

        def check_availability_with_status(self, **kwargs: Any) -> Any:
            raise AssertionError("unexpected")

        def lookup_patient(self, criteria: Any) -> None:
            return None

        def list_upcoming_appointments(self, **kwargs: Any) -> list[Any]:
            return []

    adapter = RetellToolCallingAdapter(
        scheduling_service=StubScheduling(),
        hold_service=CountingHoldService(),
        voice_calls=voice_calls,
    )
    key = build_retell_tool_call_idempotency_key(
        provider="retell",
        provider_call_id="call-1",
        tool_name="hold_appointment_slot",
        tool_call_id="tool-1",
    )
    voice_calls.execution_status[key] = "in_progress"

    response = adapter.execute(
        RetellToolCallRequest.model_validate(
            {
                "provider_call_id": "call-1",
                "tool_call_id": "tool-1",
                "tool_name": "hold_appointment_slot",
                "arguments": {
                    "availability_slot_id": str(uuid4()),
                    "owner_id": "call-1",
                },
            },
        ),
    )

    assert response.status == "failed"
    assert response.error_code == TOOL_EXECUTION_IN_PROGRESS_ERROR_CODE


def test_pending_booking_attempt_does_not_call_book_appointment() -> None:
    context = create_voice_booking_confirmation_context()
    hold_id = _active_hold_id(context)
    request_key = "retell:voice-booking:tool-call-pending"

    pending = VoiceBookingAttempt(
        id=uuid4(),
        idempotency_key=request_key,
        provider="retell",
        provider_call_id="retell-call-123",
        tool_call_id="tool-call-pending",
        voice_call_id=context.voice_call_id,
        conversation_id=context.conversation.id,
        hold_id=hold_id,
        availability_slot_id=context.booking_context.slot.id,
        patient_id=context.booking_context.patient.id,
        status=VoiceBookingAttemptStatus.PENDING,
    )
    context.attempt_repository.add(pending)

    with pytest.raises(VoiceBookingTemporaryFailureError) as exc_info:
        context.service.confirm_and_book(
            _build_request(context, hold_id=hold_id, idempotency_key=request_key),
        )

    assert exc_info.value.error_code == "booking_in_progress"
    assert context.tracking_booking.book_calls == []


def test_failed_booking_attempt_can_be_reclaimed() -> None:
    context = create_voice_booking_confirmation_context()
    hold_id = _active_hold_id(context)
    request_key = "retell:voice-booking:tool-call-failed"

    failed = VoiceBookingAttempt(
        id=uuid4(),
        idempotency_key=request_key,
        provider="retell",
        provider_call_id="retell-call-123",
        tool_call_id="tool-call-failed",
        voice_call_id=context.voice_call_id,
        conversation_id=context.conversation.id,
        hold_id=hold_id,
        availability_slot_id=context.booking_context.slot.id,
        patient_id=context.booking_context.patient.id,
        status=VoiceBookingAttemptStatus.FAILED,
        error_code="appointment_conflict",
    )
    context.attempt_repository.add(failed)

    result = context.service.confirm_and_book(
        _build_request(context, hold_id=hold_id, idempotency_key=request_key),
    )

    assert result.duplicate is False
    assert len(context.tracking_booking.book_calls) == 1


def test_booking_attempt_unique_claim_under_concurrent_insert(
    shared_sqlite_path: str,
) -> None:
    setup_engine = _engine_for_path(shared_sqlite_path)
    SessionLocal = _session_factory(setup_engine)

    with SessionLocal() as session:
        from app.domain.conversations.enums import ConversationChannel
        from app.models.conversations import Conversation
        from app.models.voice_calls import VoiceCall as VoiceCallModel

        conversation = Conversation(
            id=uuid4(),
            channel=ConversationChannel.VOICE,
        )
        voice_call = VoiceCallModel(
            id=uuid4(),
            provider="retell",
            provider_call_id="booking-race-call",
            conversation_id=conversation.id,
        )
        session.add_all([conversation, voice_call])
        session.commit()
        conversation_id = conversation.id
        voice_call_id = voice_call.id
    setup_engine.dispose()

    barrier = threading.Barrier(2)
    owned: list[bool] = []
    errors: list[BaseException] = []
    lock = threading.Lock()
    key = "retell:booking-race:tool-1"

    def worker() -> None:
        engine = _engine_for_path(shared_sqlite_path)
        SessionLocalWorker = _session_factory(engine)
        try:
            with SessionLocalWorker() as session:
                repository = SQLAlchemyVoiceBookingAttemptRepository(session)
                barrier.wait(timeout=5)
                attempt = VoiceBookingAttempt(
                    idempotency_key=key,
                    provider="retell",
                    provider_call_id="booking-race-call",
                    tool_call_id="tool-1",
                    voice_call_id=voice_call_id,
                    conversation_id=conversation_id,
                    status=VoiceBookingAttemptStatus.PENDING,
                )
                try:
                    repository.add(attempt)
                    session.commit()
                    with lock:
                        owned.append(True)
                except Exception:
                    session.rollback()
                    with lock:
                        owned.append(False)
        except BaseException as exc:  # noqa: BLE001
            with lock:
                errors.append(exc)
        finally:
            engine.dispose()

    threads = [threading.Thread(target=worker) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=10)

    assert errors == []
    assert sorted(owned) == [False, True]

    verify_engine = _engine_for_path(shared_sqlite_path)
    with _session_factory(verify_engine)() as session:
        repository = SQLAlchemyVoiceBookingAttemptRepository(session)
        stored = repository.get_by_idempotency_key(key)
        assert stored is not None
        assert stored.status == VoiceBookingAttemptStatus.PENDING
    verify_engine.dispose()


def test_failure_during_mutation_does_not_persist_succeeded_outcome(
    shared_sqlite_path: str,
) -> None:
    engine = _engine_for_path(shared_sqlite_path)
    SessionLocal = _session_factory(engine)
    with SessionLocal() as session:
        voice_call = _seed_voice_call(session)
        repository = SQLAlchemyVoiceCallRepository(session)
        key = build_retell_tool_call_idempotency_key(
            provider="retell",
            provider_call_id="call-concurrent-1",
            tool_name="reschedule_appointment",
            tool_call_id="tool-reschedule-1",
        )
        claim = repository.claim_tool_call_execution(
            voice_call_id=voice_call.id,
            provider="retell",
            provider_call_id="call-concurrent-1",
            event_type="retell_tool:reschedule_appointment",
            tool_call_id="tool-reschedule-1",
            idempotency_key=key,
            occurred_at=datetime.now(UTC),
        )
        assert claim.owned is True
        session.commit()

        repository.fail_tool_call_execution(
            idempotency_key=key,
            error_code="slot_unavailable",
        )
        session.commit()

        assert repository.get_tool_call_outcome_by_idempotency_key(idempotency_key=key) is None
        event = repository.get_event_by_idempotency_key(idempotency_key=key)
        assert event is not None
        assert event.event_metadata["execution_status"] == "failed"
    engine.dispose()


def test_stale_in_progress_reclaim_allowed_for_safe_retry_tool(
    shared_sqlite_path: str,
) -> None:
    engine = _engine_for_path(shared_sqlite_path)
    SessionLocal = _session_factory(engine)
    with SessionLocal() as session:
        voice_call = _seed_voice_call(session)
        repository = SQLAlchemyVoiceCallRepository(session)
        key = build_retell_tool_call_idempotency_key(
            provider="retell",
            provider_call_id="call-concurrent-1",
            tool_name="cancel_appointment",
            tool_call_id="tool-stale-cancel",
        )
        claim = repository.claim_tool_call_execution(
            voice_call_id=voice_call.id,
            provider="retell",
            provider_call_id="call-concurrent-1",
            event_type="retell_tool:cancel_appointment",
            tool_call_id="tool-stale-cancel",
            idempotency_key=key,
            occurred_at=datetime.now(UTC),
            stale_reclaim_policy=ToolExecutionStaleReclaimPolicy.ALLOW_SAFE_RETRY,
        )
        assert claim.owned is True
        session.commit()

        event = repository.get_event_by_idempotency_key(idempotency_key=key)
        assert event is not None
        event.event_metadata = build_in_progress_execution_metadata(
            claimed_at=datetime(2020, 1, 1, tzinfo=UTC),
        )
        session.add(event)
        session.commit()

        reclaimed = repository.claim_tool_call_execution(
            voice_call_id=voice_call.id,
            provider="retell",
            provider_call_id="call-concurrent-1",
            event_type="retell_tool:cancel_appointment",
            tool_call_id="tool-stale-cancel",
            idempotency_key=key,
            occurred_at=datetime.now(UTC),
            stale_reclaim_policy=ToolExecutionStaleReclaimPolicy.ALLOW_SAFE_RETRY,
        )
        session.commit()
        assert reclaimed.owned is True
        assert reclaimed.recovery_required is False
    engine.dispose()


def test_stale_in_progress_ambiguous_tool_does_not_reclaim(
    shared_sqlite_path: str,
) -> None:
    engine = _engine_for_path(shared_sqlite_path)
    SessionLocal = _session_factory(engine)
    with SessionLocal() as session:
        voice_call = _seed_voice_call(session)
        repository = SQLAlchemyVoiceCallRepository(session)
        key = build_retell_tool_call_idempotency_key(
            provider="retell",
            provider_call_id="call-concurrent-1",
            tool_name="hold_appointment_slot",
            tool_call_id="tool-stale-hold",
        )
        claim = repository.claim_tool_call_execution(
            voice_call_id=voice_call.id,
            provider="retell",
            provider_call_id="call-concurrent-1",
            event_type="retell_tool:hold_appointment_slot",
            tool_call_id="tool-stale-hold",
            idempotency_key=key,
            occurred_at=datetime.now(UTC),
            stale_reclaim_policy=ToolExecutionStaleReclaimPolicy.REQUIRE_MANUAL_RECOVERY,
        )
        assert claim.owned is True
        session.commit()

        event = repository.get_event_by_idempotency_key(idempotency_key=key)
        assert event is not None
        event.event_metadata = build_in_progress_execution_metadata(
            claimed_at=datetime(2020, 1, 1, tzinfo=UTC),
        )
        session.add(event)
        session.commit()

        refused = repository.claim_tool_call_execution(
            voice_call_id=voice_call.id,
            provider="retell",
            provider_call_id="call-concurrent-1",
            event_type="retell_tool:hold_appointment_slot",
            tool_call_id="tool-stale-hold",
            idempotency_key=key,
            occurred_at=datetime.now(UTC),
            stale_reclaim_policy=ToolExecutionStaleReclaimPolicy.REQUIRE_MANUAL_RECOVERY,
        )
        session.commit()

        assert refused.owned is False
        assert refused.recovery_required is True
        assert refused.status is VoiceToolExecutionStatus.IN_PROGRESS

        stored = repository.get_event_by_idempotency_key(idempotency_key=key)
        assert stored is not None
        assert stored.event_metadata["execution_status"] == "in_progress"
        assert read_tool_execution_recovery(stored.event_metadata) == AMBIGUOUS_DUAL_WRITE_RECOVERY
        assert "2020-01-01" in stored.event_metadata["claimed_at"]
    engine.dispose()


def test_adapter_ambiguous_stale_hold_does_not_execute_side_effect() -> None:
    from tests.test_retell_tool_adapter import TrackingVoiceCallRepository

    voice_calls = TrackingVoiceCallRepository()
    create_calls: list[dict[str, Any]] = []

    class CountingHoldService:
        ttl_seconds = 300

        def create_hold(self, **kwargs: Any) -> Any:
            create_calls.append(kwargs)
            raise AssertionError("ambiguous stale reclaim must not re-run hold create")

        def release_hold_by_id(self, **kwargs: Any) -> None:
            raise AssertionError("unexpected release")

    class StubScheduling:
        def get_available_slot_for_hold(self, availability_slot_id: Any) -> Any:
            raise AssertionError("unexpected scheduling call")

        def list_specialties(self) -> list[Any]:
            return []

        def list_doctors(self, specialty_id: Any = None) -> list[Any]:
            return []

        def check_availability(self, **kwargs: Any) -> list[Any]:
            return []

        def check_availability_with_status(self, **kwargs: Any) -> Any:
            raise AssertionError("unexpected")

        def lookup_patient(self, criteria: Any) -> None:
            return None

        def list_upcoming_appointments(self, **kwargs: Any) -> list[Any]:
            return []

    # Extend tracking repo to honor ambiguous stale recovery for hold.
    original_claim = voice_calls.claim_tool_call_execution

    def claim_with_recovery(**kwargs: Any) -> Any:
        from app.domain.voice_tool_execution import ToolExecutionClaimResult

        key = kwargs["idempotency_key"]
        if voice_calls.execution_status.get(key) == "in_progress":
            return ToolExecutionClaimResult(
                owned=False,
                status=VoiceToolExecutionStatus.IN_PROGRESS,
                outcome=None,
                recovery_required=True,
            )
        return original_claim(**kwargs)

    voice_calls.claim_tool_call_execution = claim_with_recovery  # type: ignore[method-assign]

    adapter = RetellToolCallingAdapter(
        scheduling_service=StubScheduling(),
        hold_service=CountingHoldService(),
        voice_calls=voice_calls,
    )
    key = build_retell_tool_call_idempotency_key(
        provider="retell",
        provider_call_id="call-1",
        tool_name="hold_appointment_slot",
        tool_call_id="tool-1",
    )
    voice_calls.execution_status[key] = "in_progress"

    response = adapter.execute(
        RetellToolCallRequest.model_validate(
            {
                "provider_call_id": "call-1",
                "tool_call_id": "tool-1",
                "tool_name": "hold_appointment_slot",
                "arguments": {
                    "availability_slot_id": str(uuid4()),
                    "owner_id": "call-1",
                },
            },
        ),
    )

    assert response.status == "failed"
    assert response.error_code == TOOL_EXECUTION_AMBIGUOUS_RECOVERY_ERROR_CODE
    assert create_calls == []


def test_stale_reclaim_does_not_bypass_pending_booking_attempt() -> None:
    context = create_voice_booking_confirmation_context()
    hold_id = _active_hold_id(context)
    request_key = "retell:voice-booking:tool-call-stale-pending"

    pending = VoiceBookingAttempt(
        id=uuid4(),
        idempotency_key=request_key,
        provider="retell",
        provider_call_id="retell-call-123",
        tool_call_id="tool-call-stale-pending",
        voice_call_id=context.voice_call_id,
        conversation_id=context.conversation.id,
        hold_id=hold_id,
        availability_slot_id=context.booking_context.slot.id,
        patient_id=context.booking_context.patient.id,
        status=VoiceBookingAttemptStatus.PENDING,
    )
    context.attempt_repository.add(pending)

    with pytest.raises(VoiceBookingTemporaryFailureError) as exc_info:
        context.service.confirm_and_book(
            _build_request(context, hold_id=hold_id, idempotency_key=request_key),
        )

    assert exc_info.value.error_code == "booking_in_progress"
    assert context.tracking_booking.book_calls == []
