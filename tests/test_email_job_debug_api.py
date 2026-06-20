from __future__ import annotations

from collections.abc import Generator, Sequence
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient

from app.api.dependencies import get_email_job_service
from app.domain.jobs.enums import EmailJobStatus, EmailJobType
from app.main import create_app
from app.models.email_jobs import EmailJob
from app.services.email_job_pagination import (
    EmailJobCursor,
    decode_email_job_cursor,
    encode_email_job_cursor,
)
from app.services.email_jobs import EmailJobService


def test_email_job_cursor_round_trip() -> None:
    cursor = EmailJobCursor(
        created_at=datetime(2026, 7, 1, 10, 0, tzinfo=UTC),
        id=uuid4(),
    )

    encoded = encode_email_job_cursor(cursor)
    decoded = decode_email_job_cursor(encoded)

    assert decoded == cursor


def test_email_job_service_returns_next_cursor() -> None:
    jobs = create_email_jobs(count=3)
    service = EmailJobService(repository=FakeEmailJobRepository(jobs))

    result = service.list_email_jobs(limit=2)

    assert len(result.items) == 2
    assert result.next_cursor is not None


def test_email_job_service_uses_cursor_for_next_page() -> None:
    jobs = create_email_jobs(count=3)
    service = EmailJobService(repository=FakeEmailJobRepository(jobs))
    first_page = service.list_email_jobs(limit=2)

    second_page = service.list_email_jobs(limit=2, cursor=first_page.next_cursor)

    assert len(second_page.items) == 1
    assert second_page.items[0].id == jobs[2].id
    assert second_page.next_cursor is None


@pytest.fixture()
def client_and_jobs() -> Generator[tuple[TestClient, list[EmailJob]], None, None]:
    app = create_app()
    jobs = create_email_jobs(count=3)
    service = EmailJobService(repository=FakeEmailJobRepository(jobs))

    def override_email_job_service() -> EmailJobService:
        return service

    app.dependency_overrides[get_email_job_service] = override_email_job_service

    with TestClient(app) as test_client:
        yield test_client, jobs

    app.dependency_overrides.clear()


def test_list_email_jobs_endpoint_returns_items_and_cursor(
    client_and_jobs: tuple[TestClient, list[EmailJob]],
) -> None:
    client, jobs = client_and_jobs
    response = client.get("/api/v1/email-jobs", params={"limit": 2})

    assert response.status_code == 200

    body = response.json()

    assert len(body["items"]) == 2
    assert body["next_cursor"] is not None
    assert body["items"][0]["id"] == str(jobs[0].id)
    assert body["items"][0]["payload"] == {"source": "test"}
    assert body["items"][0]["locked_by"] == "worker-1"
    assert body["items"][0]["locked_until"] is not None


def test_list_email_jobs_endpoint_filters_by_status(
    client_and_jobs: tuple[TestClient, list[EmailJob]],
) -> None:
    client, _jobs = client_and_jobs
    response = client.get("/api/v1/email-jobs", params={"status": "failed"})

    assert response.status_code == 200

    body = response.json()

    assert len(body["items"]) == 1
    assert body["items"][0]["status"] == "failed"
    assert body["items"][0]["last_error"] == "fake email provider failure"


def test_list_email_jobs_endpoint_rejects_invalid_cursor(
    client_and_jobs: tuple[TestClient, list[EmailJob]],
) -> None:
    client, _jobs = client_and_jobs
    response = client.get("/api/v1/email-jobs", params={"cursor": "not-a-valid-cursor"})

    assert response.status_code == 400
    assert response.json()["detail"] == "invalid email job cursor"


def test_get_email_job_endpoint_returns_job(
    client_and_jobs: tuple[TestClient, list[EmailJob]],
) -> None:
    client, jobs = client_and_jobs
    response = client.get(f"/api/v1/email-jobs/{jobs[0].id}")

    assert response.status_code == 200

    body = response.json()

    assert body["id"] == str(jobs[0].id)
    assert body["status"] == "pending"
    assert body["payload"] == {"source": "test"}
    assert body["locked_by"] == "worker-1"
    assert body["locked_until"] is not None


def test_get_email_job_endpoint_returns_not_found_for_missing_id(
    client_and_jobs: tuple[TestClient, list[EmailJob]],
) -> None:
    client, _jobs = client_and_jobs
    response = client.get(f"/api/v1/email-jobs/{uuid4()}")

    assert response.status_code == 404
    assert response.json()["detail"] == "email job was not found"


class FakeEmailJobRepository:
    def __init__(self, email_jobs: Sequence[EmailJob]) -> None:
        self.email_jobs = list(email_jobs)

    def add(self, email_job: EmailJob) -> EmailJob:
        self.email_jobs.append(email_job)

        return email_job

    def get_by_id(self, email_job_id: UUID) -> EmailJob | None:
        return next(
            (email_job for email_job in self.email_jobs if email_job.id == email_job_id),
            None,
        )

    def list_recent(
        self,
        *,
        limit: int,
        cursor: EmailJobCursor | None = None,
        job_type: EmailJobType | None = None,
        status: EmailJobStatus | None = None,
        appointment_id: UUID | None = None,
        patient_id: UUID | None = None,
    ) -> Sequence[EmailJob]:
        jobs = sorted(
            self.email_jobs,
            key=lambda item: (item.created_at, item.id),
            reverse=True,
        )

        if cursor is not None:
            jobs = [
                item
                for item in jobs
                if (item.created_at, item.id) < (cursor.created_at, cursor.id)
            ]

        if job_type is not None:
            jobs = [item for item in jobs if item.job_type == job_type]

        if status is not None:
            jobs = [item for item in jobs if item.status == status]

        if appointment_id is not None:
            jobs = [item for item in jobs if item.appointment_id == appointment_id]

        if patient_id is not None:
            jobs = [item for item in jobs if item.patient_id == patient_id]

        return jobs[:limit]

    def schedule_retry(
        self,
        *,
        email_job: EmailJob,
        now: datetime,
    ) -> EmailJob:
        email_job.status = EmailJobStatus.PENDING
        email_job.scheduled_for = now
        email_job.locked_by = None
        email_job.locked_until = None
        email_job.updated_at = now

        return email_job

    def create_replay(
        self,
        *,
        original_email_job: EmailJob,
        now: datetime,
    ) -> EmailJob:
        replay_job = EmailJob(
            job_type=original_email_job.job_type,
            status=EmailJobStatus.PENDING,
            appointment_id=original_email_job.appointment_id,
            patient_id=original_email_job.patient_id,
            recipient_email=original_email_job.recipient_email,
            subject=original_email_job.subject,
            body=original_email_job.body,
            attempts=0,
            max_attempts=original_email_job.max_attempts,
            locked_by=None,
            locked_until=None,
            last_error=None,
            payload={
                **original_email_job.payload,
                "replayed_from_email_job_id": str(original_email_job.id),
                "replayed_from_attempts": original_email_job.attempts,
                "replayed_from_status": original_email_job.status.value,
            },
            scheduled_for=now,
            sent_at=None,
            created_at=now,
            updated_at=now,
        )

        return self.add(replay_job)


def create_email_jobs(*, count: int) -> list[EmailJob]:
    base_time = datetime(2026, 7, 1, 10, 0, tzinfo=UTC)

    jobs: list[EmailJob] = []

    for index in range(count):
        created_at = base_time - timedelta(minutes=index)
        status = EmailJobStatus.FAILED if index == 1 else EmailJobStatus.PENDING
        locked_by = "worker-1" if status == EmailJobStatus.PENDING and index == 0 else None
        locked_until = (
            created_at + timedelta(minutes=5)
            if status == EmailJobStatus.PENDING and index == 0
            else None
        )

        jobs.append(
            EmailJob(
                id=uuid4(),
                job_type=EmailJobType.APPOINTMENT_CONFIRMATION,
                status=status,
                appointment_id=uuid4(),
                patient_id=uuid4(),
                recipient_email="patient@example.test",
                subject="Appointment confirmation",
                body="Your appointment is confirmed.",
                attempts=1 if status == EmailJobStatus.FAILED else 0,
                max_attempts=3,
                locked_by=locked_by,
                locked_until=locked_until,
                last_error=(
                    "fake email provider failure"
                    if status == EmailJobStatus.FAILED
                    else None
                ),
                payload={"source": "test"},
                scheduled_for=created_at,
                created_at=created_at,
                updated_at=created_at,
            ),
        )

    return jobs
