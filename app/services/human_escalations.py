from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, NoReturn
from uuid import UUID

from app.api.errors import APIError
from app.domain.human_escalations import (
    HumanEscalationPriority,
    HumanEscalationReason,
    HumanEscalationSource,
    HumanEscalationStatus,
)
from app.models.human_escalation import HumanEscalation
from app.repositories.human_escalations import HumanEscalationRepository
from app.services.clock import Clock, SystemClock
from app.services.human_escalation_pagination import (
    HumanEscalationCursor,
    decode_human_escalation_cursor,
    encode_human_escalation_cursor,
)

_ACTIVE_STATUSES = frozenset(
    {
        HumanEscalationStatus.OPEN,
        HumanEscalationStatus.ACKNOWLEDGED,
    },
)


class InvalidHumanEscalationLimitError(ValueError):
    """Raised when a human escalation page size is invalid."""


@dataclass(frozen=True, slots=True)
class HumanEscalationListFilters:
    status: HumanEscalationStatus | None = None
    reason: HumanEscalationReason | None = None
    priority: HumanEscalationPriority | None = None
    conversation_id: UUID | None = None
    patient_id: UUID | None = None
    appointment_id: UUID | None = None
    assigned_to: str | None = None
    unassigned: bool | None = None
    overdue: bool | None = None


def compute_due_at(
    priority: HumanEscalationPriority,
    now: datetime,
) -> datetime:
    if priority == HumanEscalationPriority.URGENT:
        return now + timedelta(minutes=15)

    if priority == HumanEscalationPriority.HIGH:
        return now + timedelta(hours=4)

    return now + timedelta(hours=24)


@dataclass(frozen=True, slots=True)
class HumanEscalationListResult:
    items: Sequence[HumanEscalation]
    next_cursor: str | None


class HumanEscalationService:
    def __init__(
        self,
        *,
        repository: HumanEscalationRepository,
        clock: Clock | None = None,
    ) -> None:
        self.repository = repository
        self._clock = clock or SystemClock()

    def create_or_get_active_escalation(
        self,
        *,
        conversation_id: UUID,
        reason: HumanEscalationReason,
        source: HumanEscalationSource = HumanEscalationSource.CHAT,
        patient_id: UUID | None = None,
        appointment_id: UUID | None = None,
        summary: str | None = None,
        created_by: str | None = None,
        handoff_context: dict[str, Any] | None = None,
    ) -> HumanEscalation:
        existing = self.repository.get_active_by_conversation_id(conversation_id)

        if existing is not None:
            return existing

        now = self._clock.now()
        escalation = HumanEscalation(
            conversation_id=conversation_id,
            patient_id=patient_id,
            appointment_id=appointment_id,
            status=HumanEscalationStatus.OPEN,
            reason=reason,
            priority=self._resolve_priority(reason),
            source=source,
            summary=summary,
            created_by=created_by,
            handoff_context=handoff_context,
            created_at=now,
            updated_at=now,
        )

        return self.repository.add(escalation)

    def current_time(self) -> datetime:
        return self._clock.now()

    def get_active_escalation_for_conversation(
        self,
        conversation_id: UUID,
    ) -> HumanEscalation | None:
        return self.repository.get_active_by_conversation_id(conversation_id)

    def get_escalation(self, escalation_id: UUID) -> HumanEscalation:
        escalation = self.repository.get_by_id(escalation_id)

        if escalation is None:
            self._raise_not_found()

        return escalation

    def list_escalations(
        self,
        *,
        limit: int,
        cursor: str | None = None,
        filters: HumanEscalationListFilters | None = None,
    ) -> HumanEscalationListResult:
        if limit < 1 or limit > 100:
            raise InvalidHumanEscalationLimitError("limit must be between 1 and 100")

        decoded_cursor = decode_human_escalation_cursor(cursor) if cursor is not None else None
        normalized_filters = filters or HumanEscalationListFilters()
        list_now = self._clock.now() if normalized_filters.overdue is not None else None
        fetched_items = list(
            self.repository.list(
                limit=limit + 1,
                cursor=decoded_cursor,
                status=normalized_filters.status,
                reason=normalized_filters.reason,
                priority=normalized_filters.priority,
                conversation_id=normalized_filters.conversation_id,
                patient_id=normalized_filters.patient_id,
                appointment_id=normalized_filters.appointment_id,
                assigned_to=normalized_filters.assigned_to,
                unassigned=normalized_filters.unassigned,
                overdue=normalized_filters.overdue,
                now=list_now,
            ),
        )

        items = fetched_items[:limit]
        next_cursor = None

        if len(fetched_items) > limit and items:
            last_item = items[-1]
            next_cursor = encode_human_escalation_cursor(
                HumanEscalationCursor(
                    created_at=last_item.created_at,
                    id=last_item.id,
                ),
            )

        return HumanEscalationListResult(
            items=items,
            next_cursor=next_cursor,
        )

    def acknowledge_escalation(
        self,
        escalation_id: UUID,
        *,
        acknowledged_by: str,
    ) -> HumanEscalation:
        escalation = self.get_escalation(escalation_id)

        if escalation.status == HumanEscalationStatus.ACKNOWLEDGED:
            return escalation

        if escalation.status not in _ACTIVE_STATUSES:
            self._raise_invalid_transition(
                "Only open escalations can be acknowledged.",
            )

        now = self._clock.now()
        escalation.status = HumanEscalationStatus.ACKNOWLEDGED
        escalation.acknowledged_at = now
        escalation.acknowledged_by = acknowledged_by
        escalation.updated_at = now

        return self.repository.update(escalation)

    def resolve_escalation(
        self,
        escalation_id: UUID,
        *,
        resolved_by: str,
        resolution_notes: str | None = None,
    ) -> HumanEscalation:
        escalation = self.get_escalation(escalation_id)

        if escalation.status == HumanEscalationStatus.RESOLVED:
            return escalation

        if escalation.status == HumanEscalationStatus.CANCELLED:
            self._raise_invalid_transition(
                "Cancelled escalations cannot be resolved.",
            )

        now = self._clock.now()
        escalation.status = HumanEscalationStatus.RESOLVED
        escalation.resolved_at = now
        escalation.resolved_by = resolved_by
        escalation.resolution_notes = resolution_notes
        escalation.updated_at = now

        return self.repository.update(escalation)

    def cancel_escalation(
        self,
        escalation_id: UUID,
        *,
        cancelled_by: str,
        notes: str | None = None,
    ) -> HumanEscalation:
        escalation = self.get_escalation(escalation_id)

        if escalation.status == HumanEscalationStatus.CANCELLED:
            return escalation

        if escalation.status == HumanEscalationStatus.RESOLVED:
            self._raise_invalid_transition(
                "Resolved escalations cannot be cancelled.",
            )

        now = self._clock.now()
        escalation.status = HumanEscalationStatus.CANCELLED
        escalation.resolved_by = cancelled_by
        escalation.resolution_notes = notes
        escalation.updated_at = now

        return self.repository.update(escalation)

    def assign_escalation(
        self,
        escalation_id: UUID,
        *,
        assigned_to: str,
    ) -> HumanEscalation:
        escalation = self.get_escalation(escalation_id)
        normalized_assigned_to = assigned_to.strip()

        if not normalized_assigned_to:
            self._raise_invalid_assignment(
                "assigned_to must be a non-empty string.",
            )

        if escalation.status not in _ACTIVE_STATUSES:
            self._raise_invalid_transition(
                "Only open or acknowledged escalations can be assigned.",
            )

        if escalation.assigned_to is not None and escalation.assigned_to == normalized_assigned_to:
            return escalation

        now = self._clock.now()
        escalation.assigned_to = normalized_assigned_to
        escalation.assigned_at = now
        escalation.due_at = compute_due_at(escalation.priority, now)

        if escalation.status == HumanEscalationStatus.OPEN:
            escalation.status = HumanEscalationStatus.ACKNOWLEDGED

        escalation.updated_at = now

        return self.repository.update(escalation)

    def unassign_escalation(
        self,
        escalation_id: UUID,
    ) -> HumanEscalation:
        escalation = self.get_escalation(escalation_id)

        if escalation.status not in _ACTIVE_STATUSES:
            self._raise_invalid_transition(
                "Only open or acknowledged escalations can be unassigned.",
            )

        if escalation.assigned_to is None and escalation.assigned_at is None:
            return escalation

        now = self._clock.now()
        escalation.assigned_to = None
        escalation.assigned_at = None
        escalation.updated_at = now

        return self.repository.update(escalation)

    def _resolve_priority(
        self,
        reason: HumanEscalationReason,
    ) -> HumanEscalationPriority:
        if reason == HumanEscalationReason.MEDICAL_EMERGENCY:
            return HumanEscalationPriority.URGENT

        if reason == HumanEscalationReason.USER_REQUESTED_HUMAN:
            return HumanEscalationPriority.HIGH

        if reason == HumanEscalationReason.REPEATED_BOOKING_CONFLICT:
            return HumanEscalationPriority.HIGH

        return HumanEscalationPriority.NORMAL

    def _raise_not_found(self) -> NoReturn:
        raise APIError(
            status_code=404,
            code="human_escalation_not_found",
            message="Human escalation was not found.",
        )

    def _raise_invalid_transition(self, message: str) -> NoReturn:
        raise APIError(
            status_code=409,
            code="invalid_human_escalation_transition",
            message=message,
        )

    def _raise_invalid_assignment(self, message: str) -> NoReturn:
        raise APIError(
            status_code=400,
            code="invalid_human_escalation_assignment",
            message=message,
        )
