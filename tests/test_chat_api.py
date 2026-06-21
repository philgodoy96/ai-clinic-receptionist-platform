from __future__ import annotations

import json
from collections.abc import Generator
from typing import cast
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient

from app.ai.fake_llm_provider import FakeLLMProvider
from app.ai.prompt_versions import get_current_receptionist_analysis_prompt_metadata
from app.ai.receptionist_prompt import build_receptionist_system_prompt
from app.api.dependencies import (
    get_appointment_hold_service,
    get_chat_receptionist_service,
    get_email_job_dispatch_publisher,
    get_email_job_service,
    get_llm_receptionist_analysis_service,
)
from app.core.config import Settings, get_settings
from app.db.session import get_db
from app.domain.conversations.enums import ConversationChannel
from app.domain.jobs.enums import EmailJobStatus, EmailJobType
from app.main import create_app
from app.messaging.email_job_dispatch import (
    EmailJobDispatchPublisherError,
    InMemoryEmailJobDispatchPublisher,
)
from app.models.email_jobs import EmailJob
from app.services.chat_receptionist import ChatReceptionistService
from app.services.conversation_health import ConversationHealthService
from app.services.conversations import ConversationCreate, ConversationService
from app.services.date_parsing import NaturalLanguageDateParser
from app.services.email_jobs import (
    AppointmentConfirmationEmailJobCreate,
    EmailJobService,
)
from app.services.human_escalations import HumanEscalationService
from app.services.human_handoff_notifications import HumanHandoffNotificationService
from app.services.llm_receptionist import LLMReceptionistAnalysisService
from app.services.slot_filling import LLMChatSlotFillingService
from app.services.time_preferences import TimePreferenceParser
from tests.test_chat_booking_confirmation_flow import (
    FULL_IDENTITY_WITH_CONFIRM,
    create_jane_doe_patient,
)
from tests.test_chat_receptionist_service import (
    FakeAppointmentHoldService,
    create_chat_receptionist_service,
)
from tests.test_conversations import FakeConversationRepository
from tests.test_email_jobs import FakeEmailJobRepository
from tests.test_human_escalations import FakeHumanEscalationRepository
from tests.test_scheduling_services import (
    create_demo_scheduling_service,
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


class FakeEmailJobService:
    def __init__(self) -> None:
        self.jobs: list[AppointmentConfirmationEmailJobCreate] = []

    def enqueue_appointment_confirmation(
        self,
        payload: AppointmentConfirmationEmailJobCreate,
    ) -> EmailJob:
        self.jobs.append(payload)
        return EmailJob(
            id=uuid4(),
            appointment_id=payload.appointment_id,
            patient_id=payload.patient_id,
            subject="Appointment confirmation",
            body="test",
        )


class FailingEmailJobDispatchPublisher:
    def publish_email_job_ready(self, *, email_job_id: UUID) -> None:
        raise EmailJobDispatchPublisherError("publish failed")


@pytest.fixture()
def booking_chat_api_client() -> Generator[BookingChatApiContext, None, None]:
    app = create_app()
    repository = FakeConversationRepository()
    conversation_service = ConversationService(repository=repository)
    hold_service = FakeAppointmentHoldService()
    chat_service = create_chat_receptionist_service(
        conversations=conversation_service,
        scheduling=create_demo_scheduling_service_with_emily_july_availability(
            patients=[create_jane_doe_patient()],
        ),
        hold_service=hold_service,
    )
    email_jobs = FakeEmailJobService()
    dispatch_publisher = InMemoryEmailJobDispatchPublisher()
    db = FakeDatabaseSession()

    def override_chat_service() -> ChatReceptionistService:
        return chat_service

    def override_db() -> Generator[FakeDatabaseSession, None, None]:
        yield db

    def override_hold_service() -> FakeAppointmentHoldService:
        return hold_service

    def override_email_job_service() -> EmailJobService:
        return cast(EmailJobService, email_jobs)

    def override_email_job_dispatch_publisher() -> InMemoryEmailJobDispatchPublisher:
        return dispatch_publisher

    app.dependency_overrides[get_chat_receptionist_service] = override_chat_service
    app.dependency_overrides[get_appointment_hold_service] = override_hold_service
    app.dependency_overrides[get_db] = override_db
    app.dependency_overrides[get_email_job_service] = override_email_job_service
    app.dependency_overrides[get_email_job_dispatch_publisher] = (
        override_email_job_dispatch_publisher
    )

    with TestClient(app) as test_client:
        yield BookingChatApiContext(
            client=test_client,
            db=db,
            repository=repository,
            chat_service=chat_service,
            email_jobs=email_jobs,
            dispatch_publisher=dispatch_publisher,
            hold_service=hold_service,
        )

    app.dependency_overrides.clear()


@pytest.fixture()
def booking_chat_api_client_failing_dispatch() -> Generator[BookingChatApiContext, None, None]:
    app = create_app()
    repository = FakeConversationRepository()
    conversation_service = ConversationService(repository=repository)
    hold_service = FakeAppointmentHoldService()
    chat_service = create_chat_receptionist_service(
        conversations=conversation_service,
        scheduling=create_demo_scheduling_service_with_emily_july_availability(
            patients=[create_jane_doe_patient()],
        ),
        hold_service=hold_service,
    )
    email_jobs = FakeEmailJobService()
    db = FakeDatabaseSession()

    def override_chat_service() -> ChatReceptionistService:
        return chat_service

    def override_db() -> Generator[FakeDatabaseSession, None, None]:
        yield db

    def override_hold_service() -> FakeAppointmentHoldService:
        return hold_service

    def override_email_job_service() -> EmailJobService:
        return cast(EmailJobService, email_jobs)

    def override_email_job_dispatch_publisher() -> FailingEmailJobDispatchPublisher:
        return FailingEmailJobDispatchPublisher()

    app.dependency_overrides[get_chat_receptionist_service] = override_chat_service
    app.dependency_overrides[get_appointment_hold_service] = override_hold_service
    app.dependency_overrides[get_db] = override_db
    app.dependency_overrides[get_email_job_service] = override_email_job_service
    app.dependency_overrides[get_email_job_dispatch_publisher] = (
        override_email_job_dispatch_publisher
    )

    with TestClient(app) as test_client:
        yield BookingChatApiContext(
            client=test_client,
            db=db,
            repository=repository,
            chat_service=chat_service,
            email_jobs=email_jobs,
            dispatch_publisher=InMemoryEmailJobDispatchPublisher(),
            hold_service=hold_service,
        )

    app.dependency_overrides.clear()


@pytest.fixture()
def human_escalation_chat_api_client() -> Generator[HumanEscalationChatApiContext, None, None]:
    app = create_app()
    repository = FakeConversationRepository()
    conversation_service = ConversationService(repository=repository)
    escalation_repository = FakeHumanEscalationRepository()
    human_escalations = HumanEscalationService(repository=escalation_repository)
    email_job_repository = FakeEmailJobRepository()
    email_jobs = EmailJobService(repository=email_job_repository)
    human_handoff_notifications = HumanHandoffNotificationService(email_jobs=email_jobs)
    chat_service = create_chat_receptionist_service(
        conversations=conversation_service,
        scheduling=create_demo_scheduling_service_with_emily_july_availability(),
        hold_service=FakeAppointmentHoldService(),
        conversation_health=ConversationHealthService(),
        human_escalations=human_escalations,
        human_handoff_notifications=human_handoff_notifications,
    )
    dispatch_publisher = InMemoryEmailJobDispatchPublisher()
    db = FakeDatabaseSession()

    def override_chat_service() -> ChatReceptionistService:
        return chat_service

    def override_db() -> Generator[FakeDatabaseSession, None, None]:
        yield db

    def override_email_job_dispatch_publisher() -> InMemoryEmailJobDispatchPublisher:
        return dispatch_publisher

    app.dependency_overrides[get_chat_receptionist_service] = override_chat_service
    app.dependency_overrides[get_db] = override_db
    app.dependency_overrides[get_email_job_dispatch_publisher] = (
        override_email_job_dispatch_publisher
    )

    with TestClient(app) as test_client:
        yield HumanEscalationChatApiContext(
            client=test_client,
            db=db,
            repository=repository,
            chat_service=chat_service,
            email_job_repository=email_job_repository,
            dispatch_publisher=dispatch_publisher,
        )

    app.dependency_overrides.clear()


@pytest.fixture()
def human_escalation_chat_api_client_failing_dispatch() -> Generator[
    HumanEscalationChatApiContext,
    None,
    None,
]:
    app = create_app()
    repository = FakeConversationRepository()
    conversation_service = ConversationService(repository=repository)
    escalation_repository = FakeHumanEscalationRepository()
    human_escalations = HumanEscalationService(repository=escalation_repository)
    email_job_repository = FakeEmailJobRepository()
    email_jobs = EmailJobService(repository=email_job_repository)
    human_handoff_notifications = HumanHandoffNotificationService(email_jobs=email_jobs)
    chat_service = create_chat_receptionist_service(
        conversations=conversation_service,
        scheduling=create_demo_scheduling_service_with_emily_july_availability(),
        hold_service=FakeAppointmentHoldService(),
        conversation_health=ConversationHealthService(),
        human_escalations=human_escalations,
        human_handoff_notifications=human_handoff_notifications,
    )
    db = FakeDatabaseSession()

    def override_chat_service() -> ChatReceptionistService:
        return chat_service

    def override_db() -> Generator[FakeDatabaseSession, None, None]:
        yield db

    def override_email_job_dispatch_publisher() -> FailingEmailJobDispatchPublisher:
        return FailingEmailJobDispatchPublisher()

    app.dependency_overrides[get_chat_receptionist_service] = override_chat_service
    app.dependency_overrides[get_db] = override_db
    app.dependency_overrides[get_email_job_dispatch_publisher] = (
        override_email_job_dispatch_publisher
    )

    with TestClient(app) as test_client:
        yield HumanEscalationChatApiContext(
            client=test_client,
            db=db,
            repository=repository,
            chat_service=chat_service,
            email_job_repository=email_job_repository,
            dispatch_publisher=InMemoryEmailJobDispatchPublisher(),
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


def test_post_chat_message_booking_success_returns_booking_confirmed(
    booking_chat_api_client: BookingChatApiContext,
) -> None:
    availability_response = booking_chat_api_client.client.post(
        "/api/v1/chat/messages",
        json={"message": "Dr. Emily Carter on 2026-07-02"},
    )
    assert availability_response.status_code == 200
    conversation_id = availability_response.json()["conversation_id"]

    hold_response = booking_chat_api_client.client.post(
        "/api/v1/chat/messages",
        json={
            "message": "I'll take 09:00",
            "conversation_id": conversation_id,
        },
    )
    assert hold_response.status_code == 200

    booking_response = booking_chat_api_client.client.post(
        "/api/v1/chat/messages",
        json={
            "message": FULL_IDENTITY_WITH_CONFIRM,
            "conversation_id": conversation_id,
        },
    )

    assert booking_response.status_code == 200
    assert booking_chat_api_client.db.committed is True

    body = booking_response.json()
    assert body["intent"] == "booking_confirmed"
    assert body["booking_confirmed"] is True
    assert body["appointment_id"]


def test_post_chat_message_human_escalation_publishes_handoff_notification_dispatch(
    human_escalation_chat_api_client: HumanEscalationChatApiContext,
) -> None:
    response = human_escalation_chat_api_client.client.post(
        "/api/v1/chat/messages",
        json={"message": "Please connect me to a human receptionist"},
    )

    assert response.status_code == 200
    assert human_escalation_chat_api_client.db.committed is True
    assert len(human_escalation_chat_api_client.email_job_repository.email_jobs) == 1

    email_job = human_escalation_chat_api_client.email_job_repository.email_jobs[0]
    assert email_job.job_type == EmailJobType.HUMAN_ESCALATION_NOTIFICATION
    assert len(human_escalation_chat_api_client.dispatch_publisher.published_messages) == 1
    assert (
        human_escalation_chat_api_client.dispatch_publisher.published_messages[0].email_job_id
        == email_job.id
    )

    body = response.json()
    assert "email_job_id" not in body
    assert body["intent"] == "human_escalation_requested"


def test_post_chat_message_handoff_dispatch_failure_does_not_fail_response(
    human_escalation_chat_api_client_failing_dispatch: HumanEscalationChatApiContext,
) -> None:
    client = human_escalation_chat_api_client_failing_dispatch

    response = client.client.post(
        "/api/v1/chat/messages",
        json={"message": "Please connect me to a human receptionist"},
    )

    assert response.status_code == 200
    assert client.db.committed is True
    assert len(client.email_job_repository.email_jobs) == 1

    email_job = client.email_job_repository.email_jobs[0]
    assert email_job.job_type == EmailJobType.HUMAN_ESCALATION_NOTIFICATION
    assert email_job.status == EmailJobStatus.PENDING
    assert client.dispatch_publisher.published_messages == []

    body = response.json()
    assert body["intent"] == "human_escalation_requested"
    assert "email_job_id" not in body


def test_post_chat_message_dispatch_failure_does_not_fail_booking(
    booking_chat_api_client_failing_dispatch: BookingChatApiContext,
) -> None:
    client = booking_chat_api_client_failing_dispatch

    availability_response = client.client.post(
        "/api/v1/chat/messages",
        json={"message": "Dr. Emily Carter on 2026-07-02"},
    )
    conversation_id = availability_response.json()["conversation_id"]

    client.client.post(
        "/api/v1/chat/messages",
        json={
            "message": "I'll take 09:00",
            "conversation_id": conversation_id,
        },
    )

    booking_response = client.client.post(
        "/api/v1/chat/messages",
        json={
            "message": FULL_IDENTITY_WITH_CONFIRM,
            "conversation_id": conversation_id,
        },
    )

    assert booking_response.status_code == 200

    body = booking_response.json()
    assert body["intent"] == "booking_confirmed"
    assert body["booking_confirmed"] is True
    assert body["appointment_id"]


@pytest.fixture()
def chat_client_with_default_fake_llm(
    monkeypatch: pytest.MonkeyPatch,
) -> Generator[ChatApiContext, None, None]:
    get_settings.cache_clear()
    monkeypatch.delenv("LLM_PROVIDER", raising=False)

    settings = Settings(_env_file=None)
    llm_analysis = get_llm_receptionist_analysis_service(settings=settings)
    assert isinstance(llm_analysis, LLMReceptionistAnalysisService)
    assert isinstance(llm_analysis.provider, FakeLLMProvider)

    app = create_app()
    repository = FakeConversationRepository()
    conversation_service = ConversationService(repository=repository)
    scheduling = create_demo_scheduling_service()
    chat_service = create_chat_receptionist_service(
        conversations=conversation_service,
        scheduling=scheduling,
        hold_service=FakeAppointmentHoldService(),
        llm_analysis=llm_analysis,
        slot_filling=LLMChatSlotFillingService(
            scheduling=scheduling,
            date_parser=NaturalLanguageDateParser(),
            time_preference_parser=TimePreferenceParser(),
        ),
        conversation_health=ConversationHealthService(),
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
    get_settings.cache_clear()


def test_chat_api_with_default_fake_llm_dependency_returns_greeting(
    chat_client_with_default_fake_llm: ChatApiContext,
) -> None:
    response = chat_client_with_default_fake_llm.client.post(
        "/api/v1/chat/messages",
        json={"message": "Hello"},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["intent"] == "greeting"

    assistant_message = chat_client_with_default_fake_llm.repository.messages[-1]
    assert assistant_message.role.value == "assistant"
    assert "llm_shadow_analysis" in assistant_message.message_metadata
    shadow = assistant_message.message_metadata["llm_shadow_analysis"]
    assert shadow["used_fallback"] is False
    assert shadow["prompt_version"] == get_current_receptionist_analysis_prompt_metadata().version
    assert build_receptionist_system_prompt() not in json.dumps(
        assistant_message.message_metadata,
    )
    assert "raw_provider_output" not in shadow


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


class BookingChatApiContext(ChatApiContext):
    def __init__(
        self,
        *,
        client: TestClient,
        db: FakeDatabaseSession,
        repository: FakeConversationRepository,
        chat_service: ChatReceptionistService,
        email_jobs: FakeEmailJobService,
        dispatch_publisher: InMemoryEmailJobDispatchPublisher,
        hold_service: FakeAppointmentHoldService,
    ) -> None:
        super().__init__(
            client=client,
            db=db,
            repository=repository,
            chat_service=chat_service,
        )
        self.email_jobs = email_jobs
        self.dispatch_publisher = dispatch_publisher
        self.hold_service = hold_service


class HumanEscalationChatApiContext(ChatApiContext):
    def __init__(
        self,
        *,
        client: TestClient,
        db: FakeDatabaseSession,
        repository: FakeConversationRepository,
        chat_service: ChatReceptionistService,
        email_job_repository: FakeEmailJobRepository,
        dispatch_publisher: InMemoryEmailJobDispatchPublisher,
    ) -> None:
        super().__init__(
            client=client,
            db=db,
            repository=repository,
            chat_service=chat_service,
        )
        self.email_job_repository = email_job_repository
        self.dispatch_publisher = dispatch_publisher


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
