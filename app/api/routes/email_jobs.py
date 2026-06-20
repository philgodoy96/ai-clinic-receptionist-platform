from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status

from app.api.dependencies import get_email_job_service
from app.domain.jobs.enums import EmailJobStatus, EmailJobType
from app.schemas.email_jobs import EmailJobListResponse, EmailJobResponse
from app.services.email_job_pagination import InvalidEmailJobCursorError
from app.services.email_jobs import (
    EmailJobListFilters,
    EmailJobNotFoundError,
    EmailJobService,
    InvalidEmailJobLimitError,
)

router = APIRouter(prefix="/api/v1/email-jobs", tags=["email-jobs"])


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