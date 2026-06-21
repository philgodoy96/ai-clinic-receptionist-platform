from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.api.dependencies import get_human_escalation_service
from app.api.errors import APIError
from app.db.session import get_db
from app.domain.human_escalations import (
    HumanEscalationPriority,
    HumanEscalationReason,
    HumanEscalationStatus,
)
from app.models.human_escalation import HumanEscalation
from app.schemas.human_escalations import (
    AcknowledgeHumanEscalationRequest,
    AssignHumanEscalationRequest,
    HumanEscalationListResponse,
    HumanEscalationResponse,
    ResolveHumanEscalationRequest,
    human_escalation_to_response,
)
from app.services.human_escalation_pagination import InvalidHumanEscalationCursorError
from app.services.human_escalations import (
    HumanEscalationListFilters,
    HumanEscalationService,
    InvalidHumanEscalationLimitError,
)

router = APIRouter(prefix="/api/v1/human-escalations", tags=["human-escalations"])


def _human_escalation_response(
    service: HumanEscalationService,
    escalation: HumanEscalation,
) -> HumanEscalationResponse:
    return human_escalation_to_response(escalation, now=service.current_time())


@router.get("", response_model=HumanEscalationListResponse)
def list_human_escalations(
    service: Annotated[HumanEscalationService, Depends(get_human_escalation_service)],
    limit: Annotated[int, Query(ge=1, le=100)] = 25,
    cursor: Annotated[str | None, Query()] = None,
    status_filter: Annotated[HumanEscalationStatus | None, Query(alias="status")] = None,
    reason: Annotated[HumanEscalationReason | None, Query()] = None,
    priority: Annotated[HumanEscalationPriority | None, Query()] = None,
    conversation_id: Annotated[UUID | None, Query()] = None,
    patient_id: Annotated[UUID | None, Query()] = None,
    appointment_id: Annotated[UUID | None, Query()] = None,
    assigned_to: Annotated[str | None, Query()] = None,
    unassigned: Annotated[bool | None, Query()] = None,
    overdue: Annotated[bool | None, Query()] = None,
) -> HumanEscalationListResponse:
    try:
        result = service.list_escalations(
            limit=limit,
            cursor=cursor,
            filters=HumanEscalationListFilters(
                status=status_filter,
                reason=reason,
                priority=priority,
                conversation_id=conversation_id,
                patient_id=patient_id,
                appointment_id=appointment_id,
                assigned_to=assigned_to,
                unassigned=unassigned,
                overdue=overdue,
            ),
        )
    except InvalidHumanEscalationCursorError as exc:
        raise APIError(
            status_code=400,
            code="invalid_human_escalation_cursor",
            message="Invalid human escalation cursor.",
        ) from exc
    except InvalidHumanEscalationLimitError as exc:
        raise APIError(
            status_code=400,
            code="invalid_human_escalation_limit",
            message="Human escalation limit must be between 1 and 100.",
        ) from exc

    return HumanEscalationListResponse(
        items=[
            _human_escalation_response(service, item) for item in result.items
        ],
        next_cursor=result.next_cursor,
    )


@router.get("/{escalation_id}", response_model=HumanEscalationResponse)
def get_human_escalation(
    escalation_id: UUID,
    service: Annotated[HumanEscalationService, Depends(get_human_escalation_service)],
) -> HumanEscalationResponse:
    escalation = service.get_escalation(escalation_id)

    return _human_escalation_response(service, escalation)


@router.post("/{escalation_id}/acknowledge", response_model=HumanEscalationResponse)
def acknowledge_human_escalation(
    escalation_id: UUID,
    payload: AcknowledgeHumanEscalationRequest,
    service: Annotated[HumanEscalationService, Depends(get_human_escalation_service)],
    db: Annotated[Session, Depends(get_db)],
) -> HumanEscalationResponse:
    try:
        escalation = service.acknowledge_escalation(
            escalation_id,
            acknowledged_by=payload.acknowledged_by,
        )
        db.commit()
        db.refresh(escalation)

        return _human_escalation_response(service, escalation)
    except APIError:
        db.rollback()
        raise
    except Exception:
        db.rollback()
        raise


@router.post("/{escalation_id}/resolve", response_model=HumanEscalationResponse)
def resolve_human_escalation(
    escalation_id: UUID,
    payload: ResolveHumanEscalationRequest,
    service: Annotated[HumanEscalationService, Depends(get_human_escalation_service)],
    db: Annotated[Session, Depends(get_db)],
) -> HumanEscalationResponse:
    try:
        escalation = service.resolve_escalation(
            escalation_id,
            resolved_by=payload.resolved_by,
            resolution_notes=payload.resolution_notes,
        )
        db.commit()
        db.refresh(escalation)

        return _human_escalation_response(service, escalation)
    except APIError:
        db.rollback()
        raise
    except Exception:
        db.rollback()
        raise


@router.post("/{escalation_id}/assign", response_model=HumanEscalationResponse)
def assign_human_escalation(
    escalation_id: UUID,
    payload: AssignHumanEscalationRequest,
    service: Annotated[HumanEscalationService, Depends(get_human_escalation_service)],
    db: Annotated[Session, Depends(get_db)],
) -> HumanEscalationResponse:
    try:
        escalation = service.assign_escalation(
            escalation_id,
            assigned_to=payload.assigned_to,
        )
        db.commit()
        db.refresh(escalation)

        return _human_escalation_response(service, escalation)
    except APIError:
        db.rollback()
        raise
    except Exception:
        db.rollback()
        raise


@router.post("/{escalation_id}/unassign", response_model=HumanEscalationResponse)
def unassign_human_escalation(
    escalation_id: UUID,
    service: Annotated[HumanEscalationService, Depends(get_human_escalation_service)],
    db: Annotated[Session, Depends(get_db)],
) -> HumanEscalationResponse:
    try:
        escalation = service.unassign_escalation(escalation_id)
        db.commit()
        db.refresh(escalation)

        return _human_escalation_response(service, escalation)
    except APIError:
        db.rollback()
        raise
    except Exception:
        db.rollback()
        raise
