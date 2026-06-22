from __future__ import annotations

from collections.abc import Generator, Sequence
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient

from app.api.dependencies import (
    get_email_job_dispatch_publisher,
    get_email_job_service,
)
from app.db.session import get_db
from app.domain.jobs.enums import EmailJobStatus, EmailJobType
from app.main import create_app
from app.messaging.email_job_dispatch import NoopEmailJobDispatchPublisher
from app.models.email_jobs import EmailJob
from app.services.email_job_metrics import EmailJobOperationalMetrics, EmailJobStatusCounts
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


@pytest.fixture()
def retry_replay_client() -> Generator[tuple[TestClient, dict[str, EmailJob]], None, None]:
    app = create_app()
    fake_db = FakeDatabaseSession()
    jobs = {
        "failed": create_email_job(status=EmailJobStatus.FAILED),
        "sent": create_email_job(status=EmailJobStatus.SENT, last_error=None, locked_by=None),
        "exhausted_failed": create_email_job(
            status=EmailJobStatus.FAILED,
            attempt_count=3,
            last_error="max attempts exceeded",
        ),
    }
    service = EmailJobService(repository=FakeEmailJobRepository(list(jobs.values())))

    def override_email_job_service() -> EmailJobService:
        return service

    def override_db() -> Generator[FakeDatabaseSession, None, None]:
        yield fake_db

    def override_email_job_dispatch_publisher() -> NoopEmailJobDispatchPublisher:
        return NoopEmailJobDispatchPublisher()

    app.dependency_overrides[get_email_job_service] = override_email_job_service
    app.dependency_overrides[get_db] = override_db
    app.dependency_overrides[get_email_job_dispatch_publisher] = (
        override_email_job_dispatch_publisher
    )

    with TestClient(app) as test_client:
        yield test_client, jobs

    app.dependency_overrides.clear()


def test_retry_email_job_endpoint_returns_200_for_failed(
    retry_replay_client: tuple[TestClient, dict[str, EmailJob]],
) -> None:
    client, jobs = retry_replay_client
    failed_job = jobs["failed"]
    response = client.post(f"/api/v1/email-jobs/{failed_job.id}/retry")

    assert response.status_code == 200

    body = response.json()

    assert body["id"] == str(failed_job.id)
    assert body["status"] == "pending"
    assert body["last_error"] == "smtp failure"


def test_retry_email_job_endpoint_returns_409_for_sent(
    retry_replay_client: tuple[TestClient, dict[str, EmailJob]],
) -> None:
    client, jobs = retry_replay_client
    response = client.post(f"/api/v1/email-jobs/{jobs['sent'].id}/retry")

    assert response.status_code == 409
    body = response.json()
    assert body["error"]["message"] == "Only failed email jobs can be retried."
    assert body["error"]["code"] == "invalid_email_job_retry_state"


def test_replay_email_job_endpoint_returns_200_for_failed(
    retry_replay_client: tuple[TestClient, dict[str, EmailJob]],
) -> None:
    client, jobs = retry_replay_client
    failed_job = jobs["exhausted_failed"]
    response = client.post(f"/api/v1/email-jobs/{failed_job.id}/replay")

    assert response.status_code == 200

    body = response.json()

    assert body["id"] == str(failed_job.id)
    assert body["status"] == "pending"
    assert body["attempt_count"] == 0
    assert body["last_error"] is None


def test_replay_email_job_endpoint_returns_409_for_sent(
    retry_replay_client: tuple[TestClient, dict[str, EmailJob]],
) -> None:
    client, jobs = retry_replay_client
    response = client.post(f"/api/v1/email-jobs/{jobs['sent'].id}/replay")

    assert response.status_code == 409
    body = response.json()
    assert body["error"]["message"] == "Only failed email jobs can be replayed."
    assert body["error"]["code"] == "invalid_email_job_replay_state"


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
    body = response.json()
    assert body["error"]["message"] == "Invalid email job cursor."
    assert body["error"]["code"] == "invalid_email_job_cursor"


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
    body = response.json()
    assert body["error"]["message"] == "Email job was not found."
    assert body["error"]["code"] == "email_job_not_found"


METRICS_NOW = datetime(2026, 7, 2, 12, 0, tzinfo=UTC)


@pytest.fixture()
def metrics_client() -> Generator[TestClient, None, None]:
    app = create_app()
    jobs = [
        create_email_job(status=EmailJobStatus.PENDING, locked_by=None),
        create_email_job(status=EmailJobStatus.PROCESSING, locked_by=None),
        create_email_job(status=EmailJobStatus.SENT, locked_by=None, last_error=None),
        create_email_job(status=EmailJobStatus.FAILED, locked_by=None),
        create_email_job(status=EmailJobStatus.DEAD_LETTER, locked_by=None),
        create_email_job(
            status=EmailJobStatus.PROCESSING,
            locked_by="worker-1",
            locked_until=METRICS_NOW + timedelta(minutes=5),
        ),
        create_email_job(
            status=EmailJobStatus.PROCESSING,
            locked_by="worker-1",
            locked_until=METRICS_NOW - timedelta(minutes=1),
        ),
        create_email_job(
            status=EmailJobStatus.PENDING,
            locked_by=None,
            next_attempt_at=METRICS_NOW - timedelta(hours=1),
        ),
    ]
    repository = FakeEmailJobRepository(jobs)

    class MetricsEmailJobService(EmailJobService):
        def get_operational_metrics(
            self,
            *,
            now: datetime | None = None,
        ) -> EmailJobOperationalMetrics:
            return repository.get_operational_metrics(now=METRICS_NOW)

    service = MetricsEmailJobService(repository=repository)

    def override_email_job_service() -> MetricsEmailJobService:
        return service

    app.dependency_overrides[get_email_job_service] = override_email_job_service

    with TestClient(app) as test_client:
        yield test_client

    app.dependency_overrides.clear()


def test_get_email_job_operational_metrics_endpoint_returns_metrics(
    metrics_client: TestClient,
) -> None:
    response = metrics_client.get("/api/v1/email-jobs/metrics")

    assert response.status_code == 200

    body = response.json()

    assert body["total_jobs"] == 8
    assert body["counts_by_status"]["pending"] == 2
    assert body["counts_by_status"]["processing"] == 3
    assert body["counts_by_status"]["sent"] == 1
    assert body["counts_by_status"]["failed"] == 1
    assert body["counts_by_status"]["dead_letter"] == 1
    assert body["locked_count"] == 1
    assert body["expired_lock_count"] == 1
    assert body["overdue_pending_count"] == 2
    assert "oldest_pending_created_at" in body
    assert "oldest_failed_created_at" in body
    assert "newest_dead_letter_created_at" in body


def test_get_email_job_operational_metrics_is_not_captured_by_id_route(
    metrics_client: TestClient,
) -> None:
    response = metrics_client.get("/api/v1/email-jobs/metrics")

    assert response.status_code == 200
    assert "counts_by_status" in response.json()


class FakeEmailJobRepository:
    def __init__(self, email_jobs: Sequence[EmailJob]) -> None:
        self.email_jobs = list(email_jobs)

    def add(self, email_job: EmailJob) -> EmailJob:
        if email_job.id is None:
            email_job.id = uuid4()

        self.email_jobs.append(email_job)

        return email_job

    def get_by_id(self, email_job_id: UUID) -> EmailJob | None:
        return next(
            (email_job for email_job in self.email_jobs if email_job.id == email_job_id),
            None,
        )

    def get_by_idempotency_key(
        self,
        *,
        job_type: EmailJobType,
        idempotency_key: str,
    ) -> EmailJob | None:
        return next(
            (
                email_job
                for email_job in self.email_jobs
                if email_job.job_type == job_type
                and email_job.idempotency_key == idempotency_key
            ),
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
        email_job.next_attempt_at = now
        email_job.locked_by = None
        email_job.locked_until = None
        email_job.updated_at = now

        return email_job

    def reset_for_replay(
        self,
        *,
        email_job: EmailJob,
        now: datetime,
    ) -> EmailJob:
        email_job.status = EmailJobStatus.PENDING
        email_job.attempt_count = 0
        email_job.next_attempt_at = now
        email_job.last_error = None
        email_job.locked_by = None
        email_job.locked_until = None
        email_job.updated_at = now

        return email_job

    def get_operational_metrics(
        self,
        *,
        now: datetime,
    ) -> EmailJobOperationalMetrics:
        jobs = self.email_jobs
        pending_jobs = [job for job in jobs if job.status == EmailJobStatus.PENDING]
        failed_jobs = [job for job in jobs if job.status == EmailJobStatus.FAILED]
        dead_letter_jobs = [job for job in jobs if job.status == EmailJobStatus.DEAD_LETTER]

        return EmailJobOperationalMetrics(
            total_jobs=len(jobs),
            counts_by_status=EmailJobStatusCounts(
                pending=len(pending_jobs),
                processing=sum(
                    1 for job in jobs if job.status == EmailJobStatus.PROCESSING
                ),
                sent=sum(1 for job in jobs if job.status == EmailJobStatus.SENT),
                failed=len(failed_jobs),
                dead_letter=len(dead_letter_jobs),
            ),
            locked_count=sum(
                1
                for job in jobs
                if job.locked_until is not None and job.locked_until >= now
            ),
            expired_lock_count=sum(
                1
                for job in jobs
                if job.status == EmailJobStatus.PROCESSING
                and job.locked_until is not None
                and job.locked_until < now
            ),
            overdue_pending_count=sum(
                1
                for job in jobs
                if job.status == EmailJobStatus.PENDING
                and (
                    job.next_attempt_at is None
                    or job.next_attempt_at <= now
                )
            ),
            oldest_pending_created_at=(
                min((job.created_at for job in pending_jobs), default=None)
            ),
            oldest_failed_created_at=(
                min((job.created_at for job in failed_jobs), default=None)
            ),
            newest_dead_letter_created_at=(
                max((job.created_at for job in dead_letter_jobs), default=None)
            ),
        )


class FakeDatabaseSession:
    def commit(self) -> None:
        return None

    def rollback(self) -> None:
        return None

    def refresh(self, instance: object) -> None:
        return None


def create_email_job(
    *,
    status: EmailJobStatus,
    attempt_count: int = 1,
    last_error: str | None = "smtp failure",
    locked_by: str | None = "worker-1",
    locked_until: datetime | None = None,
    payload: dict[str, str] | None = None,
    created_at: datetime | None = None,
    next_attempt_at: datetime | None = None,
) -> EmailJob:
    effective_created_at = created_at or datetime(2026, 7, 1, 10, 0, tzinfo=UTC)
    effective_next_attempt_at = next_attempt_at
    effective_locked_until = locked_until
    if effective_locked_until is None and locked_by is not None:
        effective_locked_until = effective_created_at + timedelta(minutes=5)

    return EmailJob(
        id=uuid4(),
        job_type=EmailJobType.APPOINTMENT_CONFIRMATION,
        status=status,
        appointment_id=uuid4(),
        patient_id=uuid4(),
        recipient_email="patient@example.test",
        subject="Appointment confirmation",
        body="Your appointment is confirmed.",
        attempt_count=attempt_count,
        max_attempts=3,
        locked_by=locked_by,
        locked_until=effective_locked_until,
        last_error=last_error,
        payload=payload or {"source": "test"},
        next_attempt_at=effective_next_attempt_at,
        created_at=effective_created_at,
        updated_at=effective_created_at,
    )


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
                attempt_count=1 if status == EmailJobStatus.FAILED else 0,
                max_attempts=3,
                locked_by=locked_by,
                locked_until=locked_until,
                last_error=(
                    "fake email provider failure"
                    if status == EmailJobStatus.FAILED
                    else None
                ),
                payload={"source": "test"},
                next_attempt_at=created_at,
                created_at=created_at,
                updated_at=created_at,
            ),
        )

    return jobs
