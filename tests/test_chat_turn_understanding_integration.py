from __future__ import annotations

import json
from collections.abc import Generator
from contextlib import contextmanager

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

import app.db.base  # noqa: F401
from app.ai.llm_provider import LLMProviderName
from app.ai.prompt_versions import get_current_receptionist_analysis_prompt_metadata
from app.core.request_context import reset_request_context, set_request_context
from app.db.base_class import Base
from app.models.chat_turn_understandings import ChatTurnUnderstanding
from app.repositories.sqlalchemy.chat_turn_understandings import (
    SQLAlchemyChatTurnUnderstandingRepository,
)
from app.repositories.sqlalchemy.conversations import SQLAlchemyConversationRepository
from app.services.chat_receptionist import ChatMessageInput, ChatReceptionistService
from app.services.chat_turn_understanding_records import ChatTurnUnderstandingRecordService
from app.services.conversations import ConversationService
from app.services.date_parsing import NaturalLanguageDateParser
from app.services.llm_receptionist import LLMReceptionistAnalysisService
from app.services.slot_filling import LLMChatSlotFillingService
from app.services.time_preferences import TimePreferenceParser
from tests.llm_provider_test_helpers import (
    StaticContentLLMProvider,
    build_receptionist_analysis_payload,
)
from tests.test_chat_receptionist_service import create_chat_receptionist_service
from tests.test_chat_turn_understanding_records import FakeChatTurnUnderstandingRepository
from tests.test_conversations import FakeConversationRepository
from tests.test_scheduling_services import create_demo_scheduling_service


@contextmanager
def request_context_scope(
    *,
    request_id: str,
    correlation_id: str,
) -> Generator[None, None, None]:
    tokens = set_request_context(
        request_id=request_id,
        correlation_id=correlation_id,
    )
    try:
        yield
    finally:
        reset_request_context(tokens)


class FailingChatTurnUnderstandingRepository(FakeChatTurnUnderstandingRepository):
    def add(self, record: ChatTurnUnderstanding) -> ChatTurnUnderstanding:
        raise RuntimeError("simulated persistence failure")

    def add_best_effort(self, record: ChatTurnUnderstanding) -> ChatTurnUnderstanding | None:
        return None


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

    with engine.connect() as connection:
        connection.exec_driver_sql("PRAGMA foreign_keys=ON")

    Base.metadata.create_all(bind=engine)

    with testing_session_local() as session:
        yield session

    Base.metadata.drop_all(bind=engine)
    engine.dispose()


TurnRecordRepository = (
    FakeChatTurnUnderstandingRepository | FailingChatTurnUnderstandingRepository
)


def _llm_chat_service(
    *,
    turn_record_repository: TurnRecordRepository | None = None,
    enable_turn_records: bool = True,
    llm_provider_content: str | None = None,
    use_sqlalchemy_conversations: Session | None = None,
) -> tuple[
    ChatReceptionistService,
    FakeChatTurnUnderstandingRepository | None,
    ConversationService,
]:
    repository: FakeChatTurnUnderstandingRepository | None
    turn_records: ChatTurnUnderstandingRecordService | None
    if enable_turn_records:
        repository = turn_record_repository or FakeChatTurnUnderstandingRepository()
        turn_records = ChatTurnUnderstandingRecordService(repository=repository)
    else:
        repository = None
        turn_records = None

    if use_sqlalchemy_conversations is not None:
        conversations = ConversationService(
            repository=SQLAlchemyConversationRepository(use_sqlalchemy_conversations),
        )
    else:
        conversations = ConversationService(repository=FakeConversationRepository())

    scheduling = create_demo_scheduling_service()
    content = llm_provider_content or build_receptionist_analysis_payload(
        intent="appointment_request",
        confidence=0.95,
        extracted={
            "specialty": "Dermatology",
            "doctor_name": "Dr. Emily Carter",
            "date": "2026-07-02",
            "time": "10:30",
            "patient_identity": {
                "full_name": None,
                "date_of_birth": None,
                "phone": None,
                "email": None,
            },
        },
    )
    llm_analysis = LLMReceptionistAnalysisService(
        provider=StaticContentLLMProvider(content),
        primary_provider_name=LLMProviderName.FAKE,
        max_primary_attempts=1,
    )
    slot_filling = LLMChatSlotFillingService(
        scheduling=scheduling,
        date_parser=NaturalLanguageDateParser(),
        time_preference_parser=TimePreferenceParser(),
    )
    service = create_chat_receptionist_service(
        conversations=conversations,
        scheduling=scheduling,
        llm_analysis=llm_analysis,
        slot_filling=slot_filling,
        chat_turn_understanding_records=turn_records,
    )

    return service, repository, conversations


def test_handle_message_persists_one_turn_understanding_record() -> None:
    service, repository, _conversations = _llm_chat_service()
    assert repository is not None

    with request_context_scope(request_id="req-chat-1", correlation_id="corr-chat-1"):
        result = service.handle_message(
            ChatMessageInput(message="I need a dermatology appointment on 2026-07-02 at 10:30"),
        )

    assert len(repository.records) == 1
    record = repository.records[0]
    assert record.conversation_id == result.conversation.id
    assert record.user_message_id == result.user_message.id
    assert record.assistant_message_id == result.assistant_message.id
    assert record.request_id == "req-chat-1"
    assert record.correlation_id == "corr-chat-1"


def test_handle_message_turn_record_contains_llm_reliability_metadata() -> None:
    service, repository, _conversations = _llm_chat_service()
    assert repository is not None

    service.handle_message(
        ChatMessageInput(message="I need a dermatology appointment on 2026-07-02 at 10:30"),
    )

    record = repository.records[0]
    assert record.provider == LLMProviderName.FAKE.value
    assert record.model is not None
    assert record.prompt_version == get_current_receptionist_analysis_prompt_metadata().version
    assert record.input_tokens is not None
    assert record.output_tokens is not None
    assert record.estimated_cost_micros is not None
    assert record.latency_ms is not None
    assert record.attempt_count is not None
    assert record.primary_attempt_count is not None
    assert record.fallback_attempt_count is not None
    assert record.used_fallback is False
    assert record.used_repair is False


def test_handle_message_turn_record_contains_extracted_fields() -> None:
    service, repository, _conversations = _llm_chat_service()
    assert repository is not None

    service.handle_message(
        ChatMessageInput(message="I need a dermatology appointment on 2026-07-02 at 10:30"),
    )

    record = repository.records[0]
    assert record.extracted_fields["specialty"] == "Dermatology"
    assert record.extracted_fields["doctor_name"] == "Dr. Emily Carter"
    assert record.extracted_fields["date"] == "2026-07-02"
    assert record.extracted_fields["time"] == "10:30"


def test_handle_message_turn_record_contains_slot_filling_fields() -> None:
    service, repository, _conversations = _llm_chat_service()
    assert repository is not None

    service.handle_message(
        ChatMessageInput(message="I need a dermatology appointment on 2026-07-02 at 10:30"),
    )

    record = repository.records[0]
    assert record.applied_fields
    assert "specialty" in record.applied_fields or "date" in record.applied_fields
    assert record.validation_outcome in {"applied", "partial"}
    assert record.assistant_message_id is not None


def test_handle_message_preserves_llm_shadow_metadata() -> None:
    baseline_service, _, _ = _llm_chat_service(enable_turn_records=False)
    wired_service, _, _ = _llm_chat_service()

    message = "I need a dermatology appointment on 2026-07-02 at 10:30"
    baseline = baseline_service.handle_message(ChatMessageInput(message=message))
    wired = wired_service.handle_message(ChatMessageInput(message=message))

    assert wired.assistant_message.message_metadata["llm_shadow_analysis"] == (
        baseline.assistant_message.message_metadata["llm_shadow_analysis"]
    )


def test_handle_message_preserves_slot_filling_metadata() -> None:
    baseline_service, _, _ = _llm_chat_service(enable_turn_records=False)
    wired_service, _, _ = _llm_chat_service()

    message = "I need a dermatology appointment on 2026-07-02 at 10:30"
    baseline = baseline_service.handle_message(ChatMessageInput(message=message))
    wired = wired_service.handle_message(ChatMessageInput(message=message))

    assert wired.assistant_message.message_metadata["slot_filling"] == (
        baseline.assistant_message.message_metadata["slot_filling"]
    )


def test_handle_message_succeeds_when_turn_record_persistence_fails() -> None:
    failing_repository = FailingChatTurnUnderstandingRepository()
    service, repository, _conversations = _llm_chat_service(
        turn_record_repository=failing_repository,
    )
    assert repository is not None

    result = service.handle_message(
        ChatMessageInput(message="I need an appointment"),
    )

    assert result.reply
    assert repository.records == []
    assert "llm_shadow_analysis" in result.assistant_message.message_metadata


def test_handle_message_without_turn_record_service_does_not_persist() -> None:
    service, _, _ = _llm_chat_service()
    service.chat_turn_understanding_records = None

    result = service.handle_message(
        ChatMessageInput(message="I need an appointment"),
    )

    assert result.reply
    assert service.chat_turn_understanding_records is None


def test_handle_message_persists_skipped_validation_when_llm_disabled() -> None:
    repository = FakeChatTurnUnderstandingRepository()
    turn_records = ChatTurnUnderstandingRecordService(repository=repository)
    conversations = ConversationService(repository=FakeConversationRepository())
    scheduling = create_demo_scheduling_service()
    service = create_chat_receptionist_service(
        conversations=conversations,
        scheduling=scheduling,
        llm_analysis=None,
        slot_filling=None,
        chat_turn_understanding_records=turn_records,
    )

    result = service.handle_message(ChatMessageInput(message="I need an appointment"))

    assert len(repository.records) == 1
    record = repository.records[0]
    assert record.conversation_id == result.conversation.id
    assert record.user_message_id == result.user_message.id
    assert record.assistant_message_id == result.assistant_message.id
    assert record.llm_intent is None
    assert record.validation_outcome == "skipped"


def test_handle_message_persists_turn_understanding_to_database(
    db_session: Session,
) -> None:
    repository = SQLAlchemyChatTurnUnderstandingRepository(db_session)
    turn_records = ChatTurnUnderstandingRecordService(repository=repository)
    service, _, _ = _llm_chat_service(
        use_sqlalchemy_conversations=db_session,
    )
    service.chat_turn_understanding_records = turn_records

    result = service.handle_message(
        ChatMessageInput(message="I need a dermatology appointment on 2026-07-02 at 10:30"),
    )
    db_session.commit()

    persisted = repository.get_by_user_message_id(result.user_message.id)

    assert persisted is not None
    assert persisted.conversation_id == result.conversation.id
    assert persisted.assistant_message_id == result.assistant_message.id
    assert persisted.llm_intent == "appointment_request"
    assert "raw_prompt" not in json.dumps(persisted.extracted_fields)
