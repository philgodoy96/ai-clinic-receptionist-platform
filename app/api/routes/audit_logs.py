from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query

from app.api.dependencies import get_audit_log_service
from app.api.errors import APIError
from app.domain.audit.enums import AuditActorType, AuditEventOutcome, AuditEventType
from app.schemas.audit_logs import AuditLogListResponse, AuditLogResponse
from app.services.audit_log_pagination import InvalidAuditLogCursorError
from app.services.audit_logs import (
    AuditLogListFilters,
    AuditLogService,
    InvalidAuditLogLimitError,
)

router = APIRouter(prefix="/api/v1/audit-logs", tags=["audit-logs"])


@router.get("", response_model=AuditLogListResponse)
def list_audit_logs(
    service: Annotated[AuditLogService, Depends(get_audit_log_service)],
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    cursor: Annotated[str | None, Query()] = None,
    event_type: Annotated[AuditEventType | None, Query()] = None,
    outcome: Annotated[AuditEventOutcome | None, Query()] = None,
    actor_type: Annotated[AuditActorType | None, Query()] = None,
    source: Annotated[str | None, Query(max_length=80)] = None,
    patient_id: Annotated[UUID | None, Query()] = None,
    appointment_id: Annotated[UUID | None, Query()] = None,
    call_id: Annotated[str | None, Query(max_length=120)] = None,
    conversation_id: Annotated[str | None, Query(max_length=120)] = None,
) -> AuditLogListResponse:
    try:
        result = service.list_logs(
            limit=limit,
            cursor=cursor,
            filters=AuditLogListFilters(
                event_type=event_type,
                outcome=outcome,
                actor_type=actor_type,
                source=source,
                patient_id=patient_id,
                appointment_id=appointment_id,
                call_id=call_id,
                conversation_id=conversation_id,
            ),
        )
    except InvalidAuditLogCursorError as exc:
        raise APIError(
            status_code=400,
            code="invalid_audit_log_cursor",
            message="Invalid audit log cursor.",
        ) from exc
    except InvalidAuditLogLimitError as exc:
        raise APIError(
            status_code=400,
            code="invalid_audit_log_limit",
            message="Audit log limit must be between 1 and 100.",
        ) from exc

    return AuditLogListResponse(
        items=[AuditLogResponse.model_validate(item) for item in result.items],
        next_cursor=result.next_cursor,
    )