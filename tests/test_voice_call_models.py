from __future__ import annotations

from collections.abc import Generator
from datetime import UTC, datetime
from uuid import uuid4

import pytest
from sqlalchemy import create_engine
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

import app.db.base  # noqa: F401
from app.db.base_class import Base
from app.domain.voice_calls.enums import NormalizedVoiceCallEventType, VoiceCallStatus
from app.models.voice_calls import VoiceCall, VoiceCallEvent


@pytest.fixture()
def db_session() -> Generator[Session, None, None]:
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    testing_session_local = sessionmaker(
        bind=engine,
        autoflush=False,
        autocommit=False,
        expire_on_commit=False,
    )

    Base.metadata.create_all(bind=engine)

    with testing_session_local() as session:
        yield session

    Base.metadata.drop_all(bind=engine)
    engine.dispose()


def test_migration_creates_tables() -> None:
    assert VoiceCall.__tablename__ == "voice_calls"
    assert VoiceCallEvent.__tablename__ == "voice_call_events"
    assert "voice_calls" in Base.metadata.tables
    assert "voice_call_events" in Base.metadata.tables

    voice_call_columns = {column.name for column in Base.metadata.tables["voice_calls"].columns}
    voice_call_event_columns = {
        column.name for column in Base.metadata.tables["voice_call_events"].columns
    }

    assert {
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
    }.issubset(voice_call_columns)
    assert {
        "id",
        "voice_call_id",
        "provider",
        "provider_call_id",
        "provider_event_id",
        "event_type",
        "normalized_event_type",
        "occurred_at",
        "sequence_number",
        "event_metadata",
        "idempotency_key",
        "created_at",
    }.issubset(voice_call_event_columns)


def test_voice_call_status_enum_values() -> None:
    assert VoiceCallStatus.CREATED.value == "created"
    assert VoiceCallStatus.IN_PROGRESS.value == "in_progress"
    assert VoiceCallStatus.ENDED.value == "ended"
    assert VoiceCallStatus.FAILED.value == "failed"
    assert VoiceCallStatus.UNKNOWN.value == "unknown"


def test_normalized_voice_call_event_type_enum_values() -> None:
    assert NormalizedVoiceCallEventType.CALL_STARTED.value == "call_started"
    assert NormalizedVoiceCallEventType.CALL_UPDATED.value == "call_updated"
    assert NormalizedVoiceCallEventType.CALL_ENDED.value == "call_ended"
    assert NormalizedVoiceCallEventType.CALL_FAILED.value == "call_failed"
    assert NormalizedVoiceCallEventType.UNKNOWN.value == "unknown"


def test_provider_call_id_uniqueness_per_provider(db_session: Session) -> None:
    db_session.add(
        VoiceCall(
            provider="retell",
            provider_call_id="call-123",
        ),
    )
    db_session.commit()

    db_session.add(
        VoiceCall(
            provider="retell",
            provider_call_id="call-123",
        ),
    )
    with pytest.raises(IntegrityError):
        db_session.commit()
    db_session.rollback()

    db_session.add(
        VoiceCall(
            provider="other",
            provider_call_id="call-123",
        ),
    )
    db_session.commit()


def test_event_idempotency_key_uniqueness(db_session: Session) -> None:
    voice_call = VoiceCall(provider="retell", provider_call_id="call-456")
    db_session.add(voice_call)
    db_session.commit()

    occurred_at = datetime.now(UTC)
    db_session.add(
        VoiceCallEvent(
            voice_call_id=voice_call.id,
            provider="retell",
            provider_call_id="call-456",
            event_type="call_started",
            occurred_at=occurred_at,
            idempotency_key="retell:call-456:call_started:1",
        ),
    )
    db_session.commit()

    db_session.add(
        VoiceCallEvent(
            voice_call_id=voice_call.id,
            provider="retell",
            provider_call_id="call-456",
            event_type="call_started",
            occurred_at=occurred_at,
            idempotency_key="retell:call-456:call_started:1",
        ),
    )
    with pytest.raises(IntegrityError):
        db_session.commit()


def test_event_metadata_column_works(db_session: Session) -> None:
    voice_call = VoiceCall(
        provider="retell",
        provider_call_id="call-789",
        event_metadata={"direction": "inbound", "source": "retell"},
    )
    db_session.add(voice_call)
    db_session.commit()

    persisted = db_session.get(VoiceCall, voice_call.id)
    assert persisted is not None
    assert persisted.event_metadata == {"direction": "inbound", "source": "retell"}
    assert isinstance(persisted.event_metadata, dict)
    assert "event_metadata" in VoiceCall.__table__.columns
    assert "metadata" not in VoiceCall.__table__.columns


def test_voice_call_event_relationship(db_session: Session) -> None:
    voice_call = VoiceCall(provider="retell", provider_call_id="call-rel")
    db_session.add(voice_call)
    db_session.flush()

    event = VoiceCallEvent(
        voice_call_id=voice_call.id,
        provider="retell",
        provider_call_id="call-rel",
        event_type="call_ended",
        normalized_event_type=NormalizedVoiceCallEventType.CALL_ENDED,
        occurred_at=datetime.now(UTC),
        event_metadata={"duration_seconds": 42},
        idempotency_key=f"retell:call-rel:call_ended:{uuid4()}",
    )
    voice_call.events.append(event)
    db_session.commit()

    persisted = db_session.get(VoiceCall, voice_call.id)
    assert persisted is not None
    assert len(persisted.events) == 1
    assert persisted.events[0].voice_call is persisted
    assert persisted.events[0].normalized_event_type == NormalizedVoiceCallEventType.CALL_ENDED
    assert persisted.events[0].event_metadata == {"duration_seconds": 42}


def test_voice_call_indexes_exist() -> None:
    voice_call_indexes = {index.name for index in VoiceCall.__table__.indexes}  # type: ignore[attr-defined]
    voice_call_event_indexes = {
        index.name for index in VoiceCallEvent.__table__.indexes  # type: ignore[attr-defined]
    }

    assert "ix_voice_calls_status_created_at" in voice_call_indexes
    assert "ix_voice_call_events_voice_call_occurred_at" in voice_call_event_indexes
    assert "ix_voice_call_events_provider_call_id" in voice_call_event_indexes
