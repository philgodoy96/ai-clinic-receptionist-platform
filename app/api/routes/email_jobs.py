import logging
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from app.api.dependencies import (
    get_email_job_dispatch_publisher,
    get_email_job_service,
)
from app.db.session import get_db
from app.domain.jobs.enums import EmailJobStatus, EmailJobType
from app.messaging.email_job_dispatch import (
    EmailJobDispatchPublisher,
    EmailJobDispatchPublisherError,
)
from app.schemas.email_job_metrics import (
    EmailJobOperationalMetricsResponse,
    EmailJobStatusCountsResponse,
)
from app.schemas.email_jobs import EmailJobListResponse, EmailJobResponse
from app.services.email_job_pagination import InvalidEmailJobCursorError
from app.services.email_jobs import (
    EmailJobListFilters,
    EmailJobNotFoundError,
    EmailJobService,
    InvalidEmailJobLimitError,
    InvalidEmailJobReplayStateError,
    InvalidEmailJobRetryStateError,
)

router = APIRouter(prefix="/api/v1/email-jobs", tags=["email-jobs"])
logger = logging.getLogger("app.email_jobs")


@router.get("", response_model=EmailJobListResponse)
def list_email_jobs(
    service: Annotated[EmailJobService, Depends(get_email_job_service)],
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    cursor: Annotated[str | None, Query()] = None,
    job_type: Annotated[EmailJobType | None, Query()] = None,
    status_filter: Annotated[EmailJobStatus | None, Query(alias="status")] = None,
    appointment_id: Annotated[UUID | None, Query()] = None,
    patient_id: Annotated[UUID | None, Query()] = None,
) -> EmailJobListResponse:
    try:
        result = service.list_email_jobs(
            limit=limit,
            cursor=cursor,
            filters=EmailJobListFilters(
                job_type=job_type,
                status=status_filter,
                appointment_id=appointment_id,
                patient_id=patient_id,
            ),
        )
    except InvalidEmailJobCursorError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="invalid email job cursor",
        ) from exc
    except InvalidEmailJobLimitError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="limit must be between 1 and 100",
        ) from exc

    return EmailJobListResponse(
        items=[EmailJobResponse.model_validate(item) for item in result.items],
        next_cursor=result.next_cursor,
    )


@router.get("/metrics", response_model=EmailJobOperationalMetricsResponse)
def get_email_job_operational_metrics(
    service: Annotated[EmailJobService, Depends(get_email_job_service)],
) -> EmailJobOperationalMetricsResponse:
    metrics = service.get_operational_metrics()
    return EmailJobOperationalMetricsResponse(
        total_jobs=metrics.total_jobs,
        counts_by_status=EmailJobStatusCountsResponse(
            pending=metrics.counts_by_status.pending,
            processing=metrics.counts_by_status.processing,
            sent=metrics.counts_by_status.sent,
            failed=metrics.counts_by_status.failed,
            dead_letter=metrics.counts_by_status.dead_letter,
        ),
        locked_count=metrics.locked_count,
        expired_lock_count=metrics.expired_lock_count,
        overdue_pending_count=metrics.overdue_pending_count,
        oldest_pending_created_at=metrics.oldest_pending_created_at,
        oldest_failed_created_at=metrics.oldest_failed_created_at,
        newest_dead_letter_created_at=metrics.newest_dead_letter_created_at,
    )


@router.get("/{email_job_id}", response_model=EmailJobResponse)
def get_email_job(
    email_job_id: UUID,
    service: Annotated[EmailJobService, Depends(get_email_job_service)],
) -> EmailJobResponse:
    try:
        email_job = service.get_email_job(email_job_id)
    except EmailJobNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="email job was not found",
        ) from exc

    return EmailJobResponse.model_validate(email_job)


@router.post("/{email_job_id}/retry", response_model=EmailJobResponse)
def retry_failed_email_job(
    email_job_id: UUID,
    db: Annotated[Session, Depends(get_db)],
    service: Annotated[EmailJobService, Depends(get_email_job_service)],
    email_job_dispatch: Annotated[
        EmailJobDispatchPublisher,
        Depends(get_email_job_dispatch_publisher),
    ],
) -> EmailJobResponse:
    try:
        email_job = service.retry_failed_email_job(email_job_id)
        db.commit()
        db.refresh(email_job)

        try:
            email_job_dispatch.publish_email_job_ready(email_job_id=email_job.id)
        except EmailJobDispatchPublisherError:
            logger.warning(
                "email_job_dispatch_publish_failed",
                extra={
                    "event": "email_job_dispatch_publish_failed",
                    "email_job_id": str(email_job.id),
                    "action": "retry",
                },
            )

        return EmailJobResponse.model_validate(email_job)
    except EmailJobNotFoundError as exc:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="email job was not found",
        ) from exc
    except InvalidEmailJobRetryStateError as exc:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="only failed email jobs can be retried",
        ) from exc
    except Exception:
        db.rollback()
        raise


@router.post("/{email_job_id}/replay", response_model=EmailJobResponse)
def replay_dead_letter_email_job(
    email_job_id: UUID,
    db: Annotated[Session, Depends(get_db)],
    service: Annotated[EmailJobService, Depends(get_email_job_service)],
    email_job_dispatch: Annotated[
        EmailJobDispatchPublisher,
        Depends(get_email_job_dispatch_publisher),
    ],
) -> EmailJobResponse:
    try:
        replayed_email_job = service.replay_dead_letter_email_job(email_job_id)
        db.commit()
        db.refresh(replayed_email_job)

        try:
            email_job_dispatch.publish_email_job_ready(
                email_job_id=replayed_email_job.id,
            )
        except EmailJobDispatchPublisherError:
            logger.warning(
                "email_job_dispatch_publish_failed",
                extra={
                    "event": "email_job_dispatch_publish_failed",
                    "email_job_id": str(replayed_email_job.id),
                    "action": "replay",
                },
            )

        return EmailJobResponse.model_validate(replayed_email_job)
    except EmailJobNotFoundError as exc:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="email job was not found",
        ) from exc
    except InvalidEmailJobReplayStateError as exc:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="only dead-letter email jobs can be replayed",
        ) from exc
    except Exception:
        db.rollback()
        raise