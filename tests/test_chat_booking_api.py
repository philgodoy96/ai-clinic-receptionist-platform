from __future__ import annotations

from collections.abc import Generator
from typing import cast
from uuid import UUID

import pytest
from fastapi.testclient import TestClient

from app.api.dependencies import (
    get_appointment_hold_service,
    get_chat_receptionist_service,
    get_email_job_dispatch_publisher,
    get_email_job_service,
)
from app.db.session import get_db
from app.domain.jobs.enums import EmailJobType
from app.main import create_app
from app.messaging.email_job_dispatch import InMemoryEmailJobDispatchPublisher
from app.models.email_jobs import EmailJob
from app.services.chat_receptionist import ChatReceptionistService
from app.services.conversations import ConversationService
from app.services.email_jobs import (
    AppointmentConfirmationEmailJobCreate,
    AppointmentConfirmationEmailJobResult,
    EmailJobService,
    build_appointment_confirmation_idempotency_key,
)
from tests.chat_booking_flow_support import post_new_patient_booking_via_api
from tests.guardrail_test_overrides import disable_demo_guardrails_for_test_app
from tests.test_chat_receptionist_service import (
    FakeAppointmentHoldService,
    create_chat_receptionist_service,
)
from tests.test_conversations import FakeConversationRepository
from tests.test_scheduling_services import (
    create_demo_scheduling_service_with_emily_july_availability,
)


class FakeEmailJobService:
    def __init__(self) -> None:
        from tests.test_email_jobs import FakeEmailJobRepository

        self.repository = FakeEmailJobRepository()
        self._service = EmailJobService(repository=self.repository)

    @property
    def jobs(self) -> list[EmailJob]:
        return self.repository.email_jobs

    def get_by_idempotency_key(
        self,
        *,
        job_type: EmailJobType,
        idempotency_key: str,
    ) -> EmailJob | None:
        return self._service.get_by_idempotency_key(
            job_type=job_type,
            idempotency_key=idempotency_key,
        )

    def get_or_create_appointment_confirmation_email_job(
        self,
        payload: AppointmentConfirmationEmailJobCreate,
    ) -> AppointmentConfirmationEmailJobResult:
        return self._service.get_or_create_appointment_confirmation_email_job(payload)

    def enqueue_appointment_confirmation(
        self,
        payload: AppointmentConfirmationEmailJobCreate,
    ) -> EmailJob:
        return self._service.enqueue_appointment_confirmation(payload)


class ChatBookingApiContext:
    def __init__(
        self,
        *,
        client: TestClient,
        db: FakeDatabaseSession,
        chat_service: ChatReceptionistService,
        email_jobs: FakeEmailJobService,
        dispatch_publisher: InMemoryEmailJobDispatchPublisher,
        hold_service: FakeAppointmentHoldService,
    ) -> None:
        self.client = client
        self.db = db
        self.chat_service = chat_service
        self.email_jobs = email_jobs
        self.dispatch_publisher = dispatch_publisher
        self.hold_service = hold_service


class FakeDatabaseSession:
    def __init__(self) -> None:
        self.committed = False
        self.rolled_back = False

    def commit(self) -> None:
        self.committed = True

    def rollback(self) -> None:
        self.rolled_back = True

    def refresh(self, instance: object) -> None:
        return None


@pytest.fixture()
def chat_booking_client() -> Generator[ChatBookingApiContext, None, None]:
    app = create_app()
    disable_demo_guardrails_for_test_app(app)
    repository = FakeConversationRepository()
    conversations = ConversationService(repository=repository)
    hold_service = FakeAppointmentHoldService()
    scheduling = create_demo_scheduling_service_with_emily_july_availability(
        patients=[],
    )
    chat_service = create_chat_receptionist_service(
        conversations=conversations,
        scheduling=scheduling,
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
        yield ChatBookingApiContext(
            client=test_client,
            db=db,
            chat_service=chat_service,
            email_jobs=email_jobs,
            dispatch_publisher=dispatch_publisher,
            hold_service=hold_service,
        )

    app.dependency_overrides.clear()


def test_chat_booking_enqueues_email_and_publishes_dispatch_after_commit(
    chat_booking_client: ChatBookingApiContext,
) -> None:
    availability_response = chat_booking_client.client.post(
        "/api/v1/chat/messages",
        json={"message": "Dr. Emily Carter on 2026-07-02"},
    )
    conversation_id = availability_response.json()["conversation_id"]

    hold_response = chat_booking_client.client.post(
        "/api/v1/chat/messages",
        json={
            "message": "I'll take 09:00",
            "conversation_id": conversation_id,
        },
    )
    assert hold_response.status_code == 200

    booking_response = post_new_patient_booking_via_api(
        chat_booking_client.client,
        conversation_id,
    )

    assert booking_response.status_code == 200
    assert chat_booking_client.db.committed is True

    body = booking_response.json()
    assert body["intent"] == "booking_confirmed"
    assert body["booking_confirmed"] is True
    assert body["appointment_id"]
    assert len(chat_booking_client.email_jobs.jobs) == 1

    email_job = chat_booking_client.email_jobs.jobs[0]
    assert email_job.payload["source"] == "chat_booking"
    assert email_job.recipient_email == "jane.doe@example.com"
    assert email_job.payload["patient_name"] == "Jane Doe"
    assert email_job.payload["doctor_name"] == "Dr. Emily Carter"
    assert email_job.idempotency_key == build_appointment_confirmation_idempotency_key(
        email_job.appointment_id,
    )
    assert len(chat_booking_client.dispatch_publisher.published_messages) == 1
    assert chat_booking_client.dispatch_publisher.published_messages[0].email_job_id is not None

    slot = chat_booking_client.chat_service.scheduling.availability_slots.get_by_id(
        UUID("11111111-1111-4111-8111-111111111101"),
    )
    assert slot is not None
    stored_hold = chat_booking_client.hold_service.repository.get(
        doctor_id=slot.doctor_id,
        start_time=slot.start_time,
    )
    assert stored_hold is None
