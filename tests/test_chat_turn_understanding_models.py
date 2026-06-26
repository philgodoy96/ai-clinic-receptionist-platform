from __future__ import annotations

from collections.abc import Generator
from typing import cast

import pytest
from sqlalchemy import Table, create_engine
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

import app.db.base  # noqa: F401
from app.db.base_class import Base
from app.domain.conversations.enums import ConversationChannel, ConversationMessageRole
from app.models.chat_turn_understandings import ChatTurnUnderstanding
from app.models.conversations import Conversation, ConversationMessage


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


def test_chat_turn_understanding_table_shape() -> None:
    assert ChatTurnUnderstanding.__tablename__ == "chat_turn_understandings"
    assert "chat_turn_understandings" in Base.metadata.tables

    columns = set(ChatTurnUnderstanding.__table__.columns.keys())

    assert {
        "id",
        "conversation_id",
        "user_message_id",
        "assistant_message_id",
        "request_id",
        "correlation_id",
        "provider",
        "model",
        "primary_provider",
        "fallback_provider",
        "used_fallback_provider",
        "prompt_name",
        "prompt_version",
        "schema_name",
        "schema_version",
        "llm_intent",
        "llm_confidence",
        "llm_urgency",
        "requires_human",
        "safety_flags",
        "extracted_fields",
        "normalized_fields",
        "applied_fields",
        "rejected_fields",
        "validation_outcome",
        "latency_ms",
        "input_tokens",
        "output_tokens",
        "estimated_cost_micros",
        "attempt_count",
        "primary_attempt_count",
        "fallback_attempt_count",
        "used_fallback",
        "used_repair",
        "failure_reason",
        "failure_category",
        "error",
        "created_at",
    }.issubset(columns)
    assert "content" not in columns
    assert "raw_prompt" not in columns
    assert "system_prompt" not in columns
    assert "raw_provider_output" not in columns
    assert "updated_at" not in columns


def test_chat_turn_understanding_constraints_and_indexes() -> None:
    table = cast(Table, ChatTurnUnderstanding.__table__)
    constraint_names = {constraint.name for constraint in table.constraints}
    index_names = {index.name for index in table.indexes}
    foreign_key_names: set[str] = set()
    for foreign_key in table.foreign_keys:
        if foreign_key.constraint is None:
            continue
        constraint_name = foreign_key.constraint.name
        if isinstance(constraint_name, str):
            foreign_key_names.add(constraint_name)

    assert "uq_chat_turn_understandings_user_message_id" in constraint_names
    assert "fk_chat_turn_understandings_conversation_id_conversations" in foreign_key_names
    assert (
        "fk_chat_turn_understandings_user_message_id_conversation_messages"
        in foreign_key_names
    )
    assert (
        "fk_chat_turn_understandings_assistant_message_id_conversation_messages"
        in foreign_key_names
    )
    assert "ix_chat_turn_understandings_conversation_created_at" in index_names
    assert "ix_chat_turn_understandings_user_message_id" in index_names
    assert "ix_chat_turn_understandings_assistant_message_id" in index_names
    assert "ix_chat_turn_understandings_prompt_version_created_at" in index_names
    assert "ix_chat_turn_understandings_failure_category" in index_names


def test_chat_turn_understanding_json_defaults_and_created_at(
    db_session: Session,
) -> None:
    conversation = Conversation(channel=ConversationChannel.CHAT)
    user_message = ConversationMessage(
        conversation=conversation,
        role=ConversationMessageRole.USER,
        content="I need to book an appointment",
    )
    assistant_message = ConversationMessage(
        conversation=conversation,
        role=ConversationMessageRole.ASSISTANT,
        content="I can help with that.",
    )
    db_session.add_all([conversation, user_message, assistant_message])
    db_session.flush()

    understanding = ChatTurnUnderstanding(
        conversation_id=conversation.id,
        user_message_id=user_message.id,
        assistant_message_id=assistant_message.id,
        provider="groq",
        model="llama",
        prompt_name="chat_turn_understanding",
        prompt_version="v1",
        llm_intent="book_appointment",
    )

    db_session.add(understanding)
    db_session.commit()

    persisted = db_session.get(ChatTurnUnderstanding, understanding.id)

    assert persisted is not None
    assert persisted.safety_flags == {}
    assert persisted.extracted_fields == {}
    assert persisted.normalized_fields == {}
    assert persisted.applied_fields == {}
    assert persisted.rejected_fields == {}
    assert persisted.created_at.tzinfo is not None


def test_chat_turn_understanding_allows_one_record_per_user_message(
    db_session: Session,
) -> None:
    conversation = Conversation(channel=ConversationChannel.CHAT)
    user_message = ConversationMessage(
        conversation=conversation,
        role=ConversationMessageRole.USER,
        content="Do you have availability tomorrow?",
    )
    db_session.add_all([conversation, user_message])
    db_session.flush()

    db_session.add(
        ChatTurnUnderstanding(
            conversation_id=conversation.id,
            user_message_id=user_message.id,
        ),
    )
    db_session.commit()

    db_session.add(
        ChatTurnUnderstanding(
            conversation_id=conversation.id,
            user_message_id=user_message.id,
        ),
    )
    with pytest.raises(IntegrityError):
        db_session.commit()
