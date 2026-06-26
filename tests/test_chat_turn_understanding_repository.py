from __future__ import annotations

from collections.abc import Generator
from datetime import UTC, datetime, timedelta
from uuid import UUID

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

import app.db.base  # noqa: F401
from app.db.base_class import Base
from app.domain.conversations.enums import ConversationChannel, ConversationMessageRole
from app.models.chat_turn_understandings import ChatTurnUnderstanding
from app.models.conversations import Conversation, ConversationMessage
from app.repositories.sqlalchemy.chat_turn_understandings import (
    SQLAlchemyChatTurnUnderstandingRepository,
)


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


def _seed_conversation_with_messages(
    db_session: Session,
) -> tuple[Conversation, ConversationMessage, ConversationMessage]:
    conversation = Conversation(channel=ConversationChannel.CHAT)
    user_message = ConversationMessage(
        conversation=conversation,
        role=ConversationMessageRole.USER,
        content="I need an appointment tomorrow",
    )
    assistant_message = ConversationMessage(
        conversation=conversation,
        role=ConversationMessageRole.ASSISTANT,
        content="I can help with that.",
    )
    db_session.add_all([conversation, user_message, assistant_message])
    db_session.flush()

    return conversation, user_message, assistant_message


def _build_understanding(
    *,
    conversation_id: UUID,
    user_message_id: UUID,
    assistant_message_id: UUID | None = None,
    llm_intent: str = "appointment_request",
    created_at: datetime | None = None,
) -> ChatTurnUnderstanding:
    record = ChatTurnUnderstanding(
        conversation_id=conversation_id,
        user_message_id=user_message_id,
        assistant_message_id=assistant_message_id,
        llm_intent=llm_intent,
    )
    if created_at is not None:
        record.created_at = created_at

    return record


def test_add_and_get_by_id(db_session: Session) -> None:
    conversation, user_message, assistant_message = _seed_conversation_with_messages(db_session)
    repository = SQLAlchemyChatTurnUnderstandingRepository(db_session)
    record = _build_understanding(
        conversation_id=conversation.id,
        user_message_id=user_message.id,
        assistant_message_id=assistant_message.id,
    )

    persisted = repository.add(record)

    assert persisted.id is not None
    fetched = repository.get_by_id(persisted.id)
    assert fetched is not None
    assert fetched.user_message_id == user_message.id
    assert fetched.assistant_message_id == assistant_message.id


def test_get_by_user_message_id(db_session: Session) -> None:
    conversation, user_message, assistant_message = _seed_conversation_with_messages(db_session)
    repository = SQLAlchemyChatTurnUnderstandingRepository(db_session)
    record = repository.add(
        _build_understanding(
            conversation_id=conversation.id,
            user_message_id=user_message.id,
            assistant_message_id=assistant_message.id,
        ),
    )

    fetched = repository.get_by_user_message_id(user_message.id)

    assert fetched is not None
    assert fetched.id == record.id


def test_list_by_conversation_id_orders_newest_first(db_session: Session) -> None:
    conversation, first_user_message, _assistant_message = _seed_conversation_with_messages(
        db_session,
    )
    second_user_message = ConversationMessage(
        conversation=conversation,
        role=ConversationMessageRole.USER,
        content="Can we do Friday instead?",
    )
    db_session.add(second_user_message)
    db_session.flush()

    repository = SQLAlchemyChatTurnUnderstandingRepository(db_session)
    older = repository.add(
        _build_understanding(
            conversation_id=conversation.id,
            user_message_id=first_user_message.id,
            llm_intent="appointment_request",
            created_at=datetime(2026, 6, 1, 10, 0, tzinfo=UTC),
        ),
    )
    newer = repository.add(
        _build_understanding(
            conversation_id=conversation.id,
            user_message_id=second_user_message.id,
            llm_intent="availability_request",
            created_at=datetime(2026, 6, 1, 11, 0, tzinfo=UTC),
        ),
    )

    records = repository.list_by_conversation_id(conversation.id)

    assert [record.id for record in records] == [newer.id, older.id]


def test_list_by_conversation_id_respects_limit(db_session: Session) -> None:
    conversation, user_message, _assistant_message = _seed_conversation_with_messages(db_session)
    repository = SQLAlchemyChatTurnUnderstandingRepository(db_session)
    base_time = datetime(2026, 6, 1, 10, 0, tzinfo=UTC)

    for index in range(3):
        message = ConversationMessage(
            conversation=conversation,
            role=ConversationMessageRole.USER,
            content=f"Message {index}",
        )
        db_session.add(message)
        db_session.flush()
        repository.add(
            _build_understanding(
                conversation_id=conversation.id,
                user_message_id=message.id,
                created_at=base_time + timedelta(minutes=index),
            ),
        )

    records = repository.list_by_conversation_id(conversation.id, limit=2)

    assert len(records) == 2


def test_add_flushes_without_commit(db_session: Session) -> None:
    conversation, user_message, assistant_message = _seed_conversation_with_messages(db_session)
    repository = SQLAlchemyChatTurnUnderstandingRepository(db_session)

    record = repository.add(
        _build_understanding(
            conversation_id=conversation.id,
            user_message_id=user_message.id,
            assistant_message_id=assistant_message.id,
        ),
    )

    assert record.id is not None
    assert db_session.get(ChatTurnUnderstanding, record.id) is not None

    db_session.rollback()

    assert db_session.get(ChatTurnUnderstanding, record.id) is None


def test_add_best_effort_returns_none_on_duplicate_user_message(db_session: Session) -> None:
    conversation, user_message, assistant_message = _seed_conversation_with_messages(db_session)
    repository = SQLAlchemyChatTurnUnderstandingRepository(db_session)
    repository.add(
        _build_understanding(
            conversation_id=conversation.id,
            user_message_id=user_message.id,
            assistant_message_id=assistant_message.id,
        ),
    )
    db_session.commit()

    duplicate = _build_understanding(
        conversation_id=conversation.id,
        user_message_id=user_message.id,
        assistant_message_id=assistant_message.id,
    )

    result = repository.add_best_effort(duplicate)

    assert result is None
