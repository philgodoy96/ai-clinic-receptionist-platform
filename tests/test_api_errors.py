from __future__ import annotations

from collections.abc import Generator, Sequence
from datetime import datetime
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient

from app.adapters.retell.scheduling_tools import RetellSchedulingToolAdapter
from app.api.dependencies import (
    get_email_job_service,
    get_retell_scheduling_tool_adapter,
)
from app.domain.jobs.enums import EmailJobStatus, EmailJobType
from app.main import create_app
from app.models.email_jobs import EmailJob
from app.models.scheduling import Appointment, AvailabilitySlot, Doctor, Patient, Specialty
from app.services.email_job_metrics import EmailJobOperationalMetrics, EmailJobStatusCounts
from app.services.email_job_pagination import EmailJobCursor
from app.services.email_jobs import EmailJobService
from app.services.scheduling import PatientLookupCriteria
from tests.retell_webhook_support import configure_retell_for_tests


@pytest.fixture()
def email_job_client() -> Generator[TestClient, None, None]:
    app = create_app()
    service = EmailJobService(repository=FakeEmailJobRepository([]))

    def override_email_job_service() -> EmailJobService:
        return service

    app.dependency_overrides[get_email_job_service] = override_email_job_service

    with TestClient(app) as test_client:
        yield test_client

    app.dependency_overrides.clear()


@pytest.fixture()
def retell_client() -> Generator[TestClient, None, None]:
    app = create_app()
    configure_retell_for_tests(app)
    fake_service = EmptySchedulingService()

    def override_adapter() -> RetellSchedulingToolAdapter:
        return RetellSchedulingToolAdapter(fake_service)

    app.dependency_overrides[get_retell_scheduling_tool_adapter] = override_adapter

    with TestClient(app) as test_client:
        yield test_client

    app.dependency_overrides.clear()


def test_http_404_error_uses_standardized_envelope(email_job_client: TestClient) -> None:
    response = email_job_client.get(f"/api/v1/email-jobs/{uuid4()}")

    assert response.status_code == 404

    body = response.json()

    assert "error" in body
    assert body["error"]["code"] == "email_job_not_found"
    assert body["error"]["message"] == "Email job was not found."
    assert body["error"]["request_id"] is not None


def test_invalid_cursor_uses_standardized_envelope(email_job_client: TestClient) -> None:
    response = email_job_client.get("/api/v1/email-jobs", params={"cursor": "bad-cursor"})

    assert response.status_code == 400

    body = response.json()

    assert "error" in body
    assert body["error"]["code"] == "invalid_email_job_cursor"
    assert body["error"]["message"] == "Invalid email job cursor."


def test_validation_error_uses_standardized_envelope_for_invalid_uuid_path(
    email_job_client: TestClient,
) -> None:
    response = email_job_client.get("/api/v1/email-jobs/not-a-uuid")

    assert response.status_code == 422

    body = response.json()
    error = body["error"]

    assert error["code"] == "validation_error"
    assert error["message"] == "Request validation failed."
    assert error["details"] is not None
    assert "errors" in error["details"]
    assert len(error["details"]["errors"]) >= 1

    for validation_error in error["details"]["errors"]:
        assert "input" not in validation_error


def test_validation_error_uses_standardized_envelope_for_invalid_query_limit(
    email_job_client: TestClient,
) -> None:
    response = email_job_client.get("/api/v1/email-jobs", params={"limit": 0})

    assert response.status_code == 422

    body = response.json()
    error = body["error"]

    assert error["code"] == "validation_error"
    assert error["message"] == "Request validation failed."
    assert error["details"] is not None
    assert "errors" in error["details"]
    assert len(error["details"]["errors"]) >= 1

    for validation_error in error["details"]["errors"]:
        assert "input" not in validation_error


def test_error_payload_includes_incoming_request_id(email_job_client: TestClient) -> None:
    response = email_job_client.get(
        f"/api/v1/email-jobs/{uuid4()}",
        headers={"X-Request-ID": "test-request-id"},
    )

    assert response.status_code == 404

    body = response.json()

    assert body["error"]["request_id"] == "test-request-id"


def test_retell_tool_business_errors_remain_ok_false(retell_client: TestClient) -> None:
    response = retell_client.post(
        "/api/v1/retell/tools/check-availability",
        json={
            "specialty_name": "Unknown Specialty",
            "start_from": "2026-07-01T09:00:00Z",
            "start_to": "2026-07-01T12:00:00Z",
        },
    )

    assert response.status_code == 200

    body = response.json()

    assert body["ok"] is False
    assert body["error_code"] == "specialty_not_found"
    assert "error" not in body


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
                if email_job.job_type == job_type and email_job.idempotency_key == idempotency_key
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
        return self.email_jobs[:limit]

    def schedule_retry(
        self,
        *,
        email_job: EmailJob,
        now: datetime,
    ) -> EmailJob:
        return email_job

    def reset_for_replay(
        self,
        *,
        email_job: EmailJob,
        now: datetime,
    ) -> EmailJob:
        return email_job

    def get_operational_metrics(
        self,
        *,
        now: datetime,
    ) -> EmailJobOperationalMetrics:
        return EmailJobOperationalMetrics(
            total_jobs=0,
            counts_by_status=EmailJobStatusCounts(
                pending=0,
                processing=0,
                sent=0,
                failed=0,
                dead_letter=0,
            ),
            locked_count=0,
            expired_lock_count=0,
            overdue_pending_count=0,
            oldest_pending_created_at=None,
            oldest_failed_created_at=None,
            newest_dead_letter_created_at=None,
        )


class EmptySchedulingService:
    def list_specialties(self) -> Sequence[Specialty]:
        return []

    def list_doctors(self, specialty_id: UUID | None = None) -> Sequence[Doctor]:
        return []

    def check_availability(
        self,
        *,
        doctor_id: UUID,
        start_from: datetime,
        start_to: datetime,
    ) -> Sequence[AvailabilitySlot]:
        return []

    def lookup_patient(self, criteria: PatientLookupCriteria) -> Patient | None:
        return None

    def list_upcoming_appointments(
        self,
        *,
        criteria: PatientLookupCriteria,
        start_from: datetime,
    ) -> Sequence[Appointment]:
        return []
