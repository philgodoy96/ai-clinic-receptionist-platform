from __future__ import annotations

from collections.abc import Generator
from uuid import uuid4

import pytest
from sqlalchemy import create_engine
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

import app.db.base  # noqa: F401
from app.db.base_class import Base
from app.domain.conversations.enums import ConversationChannel
from app.models.conversations import Conversation
from app.models.voice_calls import VoiceCall


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


def test_conversation_can_be_created_with_voice_channel(db_session: Session) -> None:
    conversation = Conversation(channel=ConversationChannel.VOICE)

    db_session.add(conversation)
    db_session.commit()

    persisted = db_session.get(Conversation, conversation.id)
    assert persisted is not None
    assert persisted.channel == ConversationChannel.VOICE


def test_voice_call_can_link_to_conversation(db_session: Session) -> None:
    conversation = Conversation(channel=ConversationChannel.VOICE)
    voice_call = VoiceCall(
        provider="retell",
        provider_call_id="call-linked",
        conversation=conversation,
    )

    db_session.add(voice_call)
    db_session.commit()

    persisted = db_session.get(VoiceCall, voice_call.id)
    assert persisted is not None
    assert persisted.conversation_id == conversation.id
    assert persisted.conversation is not None
    assert persisted.conversation.id == conversation.id


def test_voice_call_cannot_link_to_nonexistent_conversation(db_session: Session) -> None:
    voice_call = VoiceCall(
        provider="retell",
        provider_call_id="call-missing-conversation",
        conversation_id=uuid4(),
    )

    db_session.add(voice_call)
    with pytest.raises(IntegrityError):
        db_session.commit()


def test_existing_chat_conversation_creation_still_works(db_session: Session) -> None:
    conversation = Conversation(
        channel=ConversationChannel.CHAT,
        conversation_metadata={"source": "chat"},
    )

    db_session.add(conversation)
    db_session.commit()

    persisted = db_session.get(Conversation, conversation.id)
    assert persisted is not None
    assert persisted.channel == ConversationChannel.CHAT
    assert persisted.conversation_metadata == {"source": "chat"}


def test_voice_conversation_does_not_require_phone_number(db_session: Session) -> None:
    conversation = Conversation(
        channel=ConversationChannel.VOICE,
        patient_id=None,
        appointment_id=None,
    )

    db_session.add(conversation)
    db_session.commit()

    persisted = db_session.get(Conversation, conversation.id)
    assert persisted is not None
    assert persisted.patient_id is None
    assert persisted.appointment_id is None


def test_voice_call_can_exist_without_phone_numbers(db_session: Session) -> None:
    conversation = Conversation(channel=ConversationChannel.VOICE)
    voice_call = VoiceCall(
        provider="retell",
        provider_call_id="call-web-no-phone",
        conversation=conversation,
        from_number_redacted=None,
        to_number_redacted=None,
    )

    db_session.add(voice_call)
    db_session.commit()

    persisted = db_session.get(VoiceCall, voice_call.id)
    assert persisted is not None
    assert persisted.from_number_redacted is None
    assert persisted.to_number_redacted is None
    assert persisted.conversation_id == conversation.id
