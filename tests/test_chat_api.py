from __future__ import annotations

from collections.abc import Generator
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from app.api.dependencies import get_chat_receptionist_service
from app.db.session import get_db
from app.domain.conversations.enums import ConversationChannel
from app.main import create_app
from app.services.chat_receptionist import ChatReceptionistService
from app.services.conversations import ConversationCreate, ConversationService
from tests.test_chat_receptionist_service import (
    FakeAppointmentHoldService,
    create_chat_receptionist_service,
)
from tests.test_conversations import FakeConversationRepository
from tests.test_scheduling_services import (
    create_demo_scheduling_service_with_emily_july_availability,
)


@pytest.fixture()
def chat_client() -> Generator[ChatApiContext, None, None]:
    app = create_app()
    repository = FakeConversationRepository()
    conversation_service = ConversationService(repository=repository)
    chat_service = create_chat_receptionist_service(
        conversations=conversation_service,
        scheduling=create_demo_scheduling_service_with_emily_july_availability(),
        hold_service=FakeAppointmentHoldService(),
    )
    db = FakeDatabaseSession()

    def override_chat_service() -> ChatReceptionistService:
        return chat_service

    def override_db() -> Generator[FakeDatabaseSession, None, None]:
        yield db

    app.dependency_overrides[get_chat_receptionist_service] = override_chat_service
    app.dependency_overrides[get_db] = override_db

    with TestClient(app) as test_client:
        yield ChatApiContext(
            client=test_client,
            db=db,
            repository=repository,
            chat_service=chat_service,
        )

    app.dependency_overrides.clear()


def test_post_chat_message_returns_200_and_creates_conversation(
    chat_client: ChatApiContext,
) -> None:
    response = chat_client.client.post(
        "/api/v1/chat/messages",
        json={"message": "Hello"},
    )

    assert response.status_code == 200
    assert chat_client.db.committed is True
    assert len(chat_client.repository.conversations) == 1

    body = response.json()

    assert body["conversation_id"] == str(chat_client.repository.conversations[0].id)
    assert body["user_message_id"]
    assert body["assistant_message_id"]
    assert body["intent"] == "greeting"
    assert body["reply"]
    assert "error" not in body
    assert "request_id" not in body
    assert "correlation_id" not in body


def test_post_chat_message_with_existing_conversation_id_reuses_it(
    chat_client: ChatApiContext,
) -> None:
    existing = chat_client.chat_service.conversations.create_conversation(
        ConversationCreate(channel=ConversationChannel.CHAT),
    )

    response = chat_client.client.post(
        "/api/v1/chat/messages",
        json={
            "message": "Can we continue?",
            "conversation_id": str(existing.id),
        },
    )

    assert response.status_code == 200

    body = response.json()

    assert body["conversation_id"] == str(existing.id)
    assert len(chat_client.repository.conversations) == 1
    assert len(chat_client.repository.messages) == 2


def test_post_chat_message_with_unknown_conversation_id_returns_standardized_404(
    chat_client: ChatApiContext,
) -> None:
    response = chat_client.client.post(
        "/api/v1/chat/messages",
        json={
            "message": "Hello",
            "conversation_id": str(uuid4()),
        },
    )

    assert response.status_code == 404
    assert chat_client.db.rolled_back is True

    body = response.json()
    error = body["error"]

    assert error["code"] == "conversation_not_found"
    assert error["message"] == "Conversation was not found."
    assert error["request_id"] is not None


def test_post_chat_message_with_empty_message_returns_standardized_validation_error(
    chat_client: ChatApiContext,
) -> None:
    response = chat_client.client.post(
        "/api/v1/chat/messages",
        json={"message": ""},
    )

    assert response.status_code == 422

    body = response.json()
    error = body["error"]

    assert error["code"] == "validation_error"
    assert error["message"] == "Request validation failed."
    assert error["details"] is not None
    assert "errors" in error["details"]
    assert len(error["details"]["errors"]) >= 1


def test_post_chat_message_with_list_specialties_returns_200_and_intent(
    chat_client: ChatApiContext,
) -> None:
    response = chat_client.client.post(
        "/api/v1/chat/messages",
        json={"message": "What specialties do you have?"},
    )

    assert response.status_code == 200

    body = response.json()

    assert body["intent"] == "list_specialties"
    assert "Dermatology" in body["reply"]
    assert "Cardiology" in body["reply"]
    assert "Primary Care" in body["reply"]


def test_post_chat_message_with_dermatologist_request_returns_200_and_intent(
    chat_client: ChatApiContext,
) -> None:
    response = chat_client.client.post(
        "/api/v1/chat/messages",
        json={"message": "I need a dermatologist"},
    )

    assert response.status_code == 200

    body = response.json()

    assert body["intent"] == "specialty_doctors"
    assert "Dr. Emily Carter" in body["reply"]


def test_post_chat_message_with_doctor_and_date_returns_availability_results(
    chat_client: ChatApiContext,
) -> None:
    response = chat_client.client.post(
        "/api/v1/chat/messages",
        json={"message": "Dr. Emily Carter on 2026-07-02"},
    )

    assert response.status_code == 200

    body = response.json()

    assert body["intent"] == "availability_results"
    assert "09:00" in body["reply"]
    assert "10:30" in body["reply"]


def test_post_chat_message_hold_flow_returns_hold_created(
    chat_client: ChatApiContext,
) -> None:
    availability_response = chat_client.client.post(
        "/api/v1/chat/messages",
        json={"message": "Dr. Emily Carter on 2026-07-02"},
    )

    assert availability_response.status_code == 200

    availability_body = availability_response.json()
    conversation_id = availability_body["conversation_id"]

    hold_response = chat_client.client.post(
        "/api/v1/chat/messages",
        json={
            "message": "I'll take 09:00",
            "conversation_id": conversation_id,
        },
    )

    assert hold_response.status_code == 200

    hold_body = hold_response.json()

    assert hold_body["conversation_id"] == conversation_id
    assert hold_body["intent"] == "hold_created"
    assert "09:00" in hold_body["reply"]


def test_post_chat_message_with_invalid_date_returns_invalid_date_intent(
    chat_client: ChatApiContext,
) -> None:
    response = chat_client.client.post(
        "/api/v1/chat/messages",
        json={"message": "Dr. Emily Carter on 2026-99-99"},
    )

    assert response.status_code == 200

    body = response.json()

    assert body["intent"] == "invalid_date"
    assert "YYYY-MM-DD" in body["reply"]


def test_success_response_does_not_include_request_or_correlation_ids(
    chat_client: ChatApiContext,
) -> None:
    response = chat_client.client.post(
        "/api/v1/chat/messages",
        json={"message": "Hi"},
    )

    assert response.status_code == 200

    body = response.json()

    assert "request_id" not in body
    assert "correlation_id" not in body
    assert "error" not in body


class ChatApiContext:
    def __init__(
        self,
        *,
        client: TestClient,
        db: FakeDatabaseSession,
        repository: FakeConversationRepository,
        chat_service: ChatReceptionistService,
    ) -> None:
        self.client = client
        self.db = db
        self.repository = repository
        self.chat_service = chat_service


class FakeDatabaseSession:
    def __init__(self) -> None:
        self.committed = False
        self.rolled_back = False
        self.refreshed = False

    def commit(self) -> None:
        self.committed = True

    def rollback(self) -> None:
        self.rolled_back = True

    def refresh(self, instance: object) -> None:
        self.refreshed = True
