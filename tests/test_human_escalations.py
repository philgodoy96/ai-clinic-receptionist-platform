from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest

from app.api.errors import APIError
from app.domain.human_escalations import (
    HumanEscalationPriority,
    HumanEscalationReason,
    HumanEscalationSource,
    HumanEscalationStatus,
)
from app.models.human_escalation import HumanEscalation
from app.services.clock import FixedClock
from app.services.human_escalation_pagination import (
    HumanEscalationCursor,
    decode_human_escalation_cursor,
    encode_human_escalation_cursor,
)
from app.services.human_escalations import (
    HumanEscalationListFilters,
    HumanEscalationService,
    InvalidHumanEscalationLimitError,
    compute_due_at,
)


def test_human_escalation_cursor_round_trip() -> None:
    cursor = HumanEscalationCursor(
        created_at=datetime(2026, 7, 1, 10, 0, tzinfo=UTC),
        id=uuid4(),
    )

    encoded = encode_human_escalation_cursor(cursor)
    decoded = decode_human_escalation_cursor(encoded)

    assert decoded == cursor


def test_create_or_get_active_escalation_creates_open_escalation() -> None:
    repository = FakeHumanEscalationRepository()
    service = HumanEscalationService(repository=repository)
    conversation_id = uuid4()

    escalation = service.create_or_get_active_escalation(
        conversation_id=conversation_id,
        reason=HumanEscalationReason.USER_REQUESTED_HUMAN,
        summary="User asked for a human.",
        handoff_context={"selected_doctor_name": "Dr. Emily Carter"},
    )

    assert escalation.status == HumanEscalationStatus.OPEN
    assert escalation.priority == HumanEscalationPriority.HIGH
    assert escalation.source == HumanEscalationSource.CHAT
    assert escalation.handoff_context == {"selected_doctor_name": "Dr. Emily Carter"}
    assert len(repository.escalations) == 1


def test_create_or_get_active_escalation_is_idempotent_for_same_conversation() -> None:
    repository = FakeHumanEscalationRepository()
    service = HumanEscalationService(repository=repository)
    conversation_id = uuid4()

    first = service.create_or_get_active_escalation(
        conversation_id=conversation_id,
        reason=HumanEscalationReason.USER_REQUESTED_HUMAN,
    )
    second = service.create_or_get_active_escalation(
        conversation_id=conversation_id,
        reason=HumanEscalationReason.USER_REQUESTED_HUMAN,
    )

    assert second.id == first.id
    assert second.status == HumanEscalationStatus.OPEN
    assert len(repository.escalations) == 1


def test_create_or_get_active_escalation_returns_existing_without_overwriting_context() -> None:
    repository = FakeHumanEscalationRepository()
    service = HumanEscalationService(repository=repository)
    conversation_id = uuid4()

    first = service.create_or_get_active_escalation(
        conversation_id=conversation_id,
        reason=HumanEscalationReason.NO_PROGRESS,
        handoff_context={"active_hold_present": True},
    )
    second = service.create_or_get_active_escalation(
        conversation_id=conversation_id,
        reason=HumanEscalationReason.REPEATED_FALLBACK,
        handoff_context={"active_hold_present": False},
    )

    assert second.id == first.id
    assert second.handoff_context == {"active_hold_present": True}
    assert len(repository.escalations) == 1


def test_medical_emergency_priority_is_urgent() -> None:
    service = HumanEscalationService(repository=FakeHumanEscalationRepository())

    escalation = service.create_or_get_active_escalation(
        conversation_id=uuid4(),
        reason=HumanEscalationReason.MEDICAL_EMERGENCY,
    )

    assert escalation.priority == HumanEscalationPriority.URGENT


def test_user_requested_human_priority_is_high() -> None:
    service = HumanEscalationService(repository=FakeHumanEscalationRepository())

    escalation = service.create_or_get_active_escalation(
        conversation_id=uuid4(),
        reason=HumanEscalationReason.USER_REQUESTED_HUMAN,
    )

    assert escalation.priority == HumanEscalationPriority.HIGH


def test_create_or_get_active_escalation_assigns_priority_by_reason() -> None:
    repository = FakeHumanEscalationRepository()
    service = HumanEscalationService(repository=repository)

    urgent = service.create_or_get_active_escalation(
        conversation_id=uuid4(),
        reason=HumanEscalationReason.MEDICAL_EMERGENCY,
    )
    high = service.create_or_get_active_escalation(
        conversation_id=uuid4(),
        reason=HumanEscalationReason.REPEATED_BOOKING_CONFLICT,
    )
    normal = service.create_or_get_active_escalation(
        conversation_id=uuid4(),
        reason=HumanEscalationReason.REPEATED_FALLBACK,
    )

    assert urgent.priority == HumanEscalationPriority.URGENT
    assert high.priority == HumanEscalationPriority.HIGH
    assert normal.priority == HumanEscalationPriority.NORMAL


def test_get_escalation_raises_not_found_api_error() -> None:
    service = HumanEscalationService(repository=FakeHumanEscalationRepository())

    with pytest.raises(APIError) as exc_info:
        service.get_escalation(uuid4())

    assert exc_info.value.status_code == 404
    assert exc_info.value.code == "human_escalation_not_found"


def test_list_escalations_returns_next_cursor() -> None:
    escalations = create_human_escalations(count=3)
    service = HumanEscalationService(
        repository=FakeHumanEscalationRepository(escalations),
    )

    result = service.list_escalations(limit=2)

    assert len(result.items) == 2
    assert result.next_cursor is not None


def test_list_escalations_uses_cursor_for_next_page() -> None:
    escalations = create_human_escalations(count=3)
    service = HumanEscalationService(
        repository=FakeHumanEscalationRepository(escalations),
    )
    first_page = service.list_escalations(limit=2)

    second_page = service.list_escalations(limit=2, cursor=first_page.next_cursor)

    assert len(second_page.items) == 1
    assert second_page.items[0].id == escalations[2].id
    assert second_page.next_cursor is None


def test_list_escalations_applies_filters() -> None:
    escalations = create_human_escalations(count=3)
    service = HumanEscalationService(
        repository=FakeHumanEscalationRepository(escalations),
    )

    result = service.list_escalations(
        limit=10,
        filters=HumanEscalationListFilters(
            status=HumanEscalationStatus.OPEN,
            reason=HumanEscalationReason.USER_REQUESTED_HUMAN,
        ),
    )

    assert len(result.items) == 1
    assert result.items[0].id == escalations[0].id


def test_list_escalations_rejects_invalid_limit() -> None:
    service = HumanEscalationService(repository=FakeHumanEscalationRepository())

    with pytest.raises(InvalidHumanEscalationLimitError):
        service.list_escalations(limit=0)


def test_acknowledge_escalation_transitions_open_to_acknowledged() -> None:
    repository = FakeHumanEscalationRepository()
    service = HumanEscalationService(repository=repository)
    escalation = service.create_or_get_active_escalation(
        conversation_id=uuid4(),
        reason=HumanEscalationReason.USER_REQUESTED_HUMAN,
    )

    acknowledged = service.acknowledge_escalation(
        escalation.id,
        acknowledged_by="staff-1",
    )

    assert acknowledged.status == HumanEscalationStatus.ACKNOWLEDGED
    assert acknowledged.acknowledged_by == "staff-1"
    assert acknowledged.acknowledged_at is not None


def test_acknowledge_escalation_is_idempotent_when_already_acknowledged() -> None:
    repository = FakeHumanEscalationRepository()
    service = HumanEscalationService(repository=repository)
    escalation = service.create_or_get_active_escalation(
        conversation_id=uuid4(),
        reason=HumanEscalationReason.USER_REQUESTED_HUMAN,
    )
    first = service.acknowledge_escalation(
        escalation.id,
        acknowledged_by="staff-1",
    )
    second = service.acknowledge_escalation(
        escalation.id,
        acknowledged_by="staff-2",
    )

    assert second.id == first.id
    assert second.acknowledged_by == "staff-1"


def test_acknowledge_resolved_escalation_raises_conflict() -> None:
    repository = FakeHumanEscalationRepository()
    service = HumanEscalationService(repository=repository)
    escalation = service.create_or_get_active_escalation(
        conversation_id=uuid4(),
        reason=HumanEscalationReason.USER_REQUESTED_HUMAN,
    )
    service.resolve_escalation(escalation.id, resolved_by="staff-1")

    with pytest.raises(APIError) as exc_info:
        service.acknowledge_escalation(escalation.id, acknowledged_by="staff-2")

    assert exc_info.value.status_code == 409
    assert exc_info.value.code == "invalid_human_escalation_transition"


def test_resolve_acknowledged_escalation_sets_resolution_fields() -> None:
    repository = FakeHumanEscalationRepository()
    service = HumanEscalationService(repository=repository)
    escalation = service.create_or_get_active_escalation(
        conversation_id=uuid4(),
        reason=HumanEscalationReason.USER_REQUESTED_HUMAN,
    )
    service.acknowledge_escalation(
        escalation.id,
        acknowledged_by="staff-1",
    )

    resolved = service.resolve_escalation(
        escalation.id,
        resolved_by="staff-2",
        resolution_notes="Called patient and resolved scheduling issue.",
    )

    assert resolved.status == HumanEscalationStatus.RESOLVED
    assert resolved.resolved_by == "staff-2"
    assert resolved.resolution_notes == "Called patient and resolved scheduling issue."
    assert resolved.resolved_at is not None


def test_resolve_escalation_transitions_active_to_resolved() -> None:
    repository = FakeHumanEscalationRepository()
    service = HumanEscalationService(repository=repository)
    escalation = service.create_or_get_active_escalation(
        conversation_id=uuid4(),
        reason=HumanEscalationReason.NO_PROGRESS,
    )

    resolved = service.resolve_escalation(
        escalation.id,
        resolved_by="staff-1",
        resolution_notes="Handled by front desk.",
    )

    assert resolved.status == HumanEscalationStatus.RESOLVED
    assert resolved.resolved_by == "staff-1"
    assert resolved.resolution_notes == "Handled by front desk."
    assert resolved.resolved_at is not None


def test_resolve_escalation_is_idempotent_when_already_resolved() -> None:
    repository = FakeHumanEscalationRepository()
    service = HumanEscalationService(repository=repository)
    escalation = service.create_or_get_active_escalation(
        conversation_id=uuid4(),
        reason=HumanEscalationReason.NO_PROGRESS,
    )
    first = service.resolve_escalation(
        escalation.id,
        resolved_by="staff-1",
        resolution_notes="Done.",
    )
    second = service.resolve_escalation(
        escalation.id,
        resolved_by="staff-2",
        resolution_notes="Updated.",
    )

    assert second.id == first.id
    assert second.resolved_by == "staff-1"
    assert second.resolution_notes == "Done."


def test_cancel_escalation_transitions_active_to_cancelled() -> None:
    repository = FakeHumanEscalationRepository()
    service = HumanEscalationService(repository=repository)
    escalation = service.create_or_get_active_escalation(
        conversation_id=uuid4(),
        reason=HumanEscalationReason.UNKNOWN,
    )

    cancelled = service.cancel_escalation(
        escalation.id,
        cancelled_by="staff-1",
        notes="User continued in chat.",
    )

    assert cancelled.status == HumanEscalationStatus.CANCELLED
    assert cancelled.resolved_by == "staff-1"
    assert cancelled.resolution_notes == "User continued in chat."


def test_cancel_resolved_escalation_raises_conflict() -> None:
    repository = FakeHumanEscalationRepository()
    service = HumanEscalationService(repository=repository)
    escalation = service.create_or_get_active_escalation(
        conversation_id=uuid4(),
        reason=HumanEscalationReason.UNKNOWN,
    )
    service.resolve_escalation(escalation.id, resolved_by="staff-1")

    with pytest.raises(APIError) as exc_info:
        service.cancel_escalation(escalation.id, cancelled_by="staff-2")

    assert exc_info.value.status_code == 409
    assert exc_info.value.code == "invalid_human_escalation_transition"


def test_compute_due_at_for_each_priority() -> None:
    now = datetime(2026, 7, 1, 10, 0, tzinfo=UTC)

    assert compute_due_at(HumanEscalationPriority.URGENT, now) == now + timedelta(
        minutes=15,
    )
    assert compute_due_at(HumanEscalationPriority.HIGH, now) == now + timedelta(hours=4)
    assert compute_due_at(HumanEscalationPriority.NORMAL, now) == now + timedelta(
        hours=24,
    )


def test_assign_escalation_transitions_open_to_acknowledged_and_sets_sla() -> None:
    now = datetime(2026, 7, 1, 10, 0, tzinfo=UTC)
    repository = FakeHumanEscalationRepository()
    service = HumanEscalationService(
        repository=repository,
        clock=FixedClock(current_time=now),
    )
    escalation = service.create_or_get_active_escalation(
        conversation_id=uuid4(),
        reason=HumanEscalationReason.USER_REQUESTED_HUMAN,
    )

    assigned = service.assign_escalation(
        escalation.id,
        assigned_to="staff-1",
    )

    assert assigned.status == HumanEscalationStatus.ACKNOWLEDGED
    assert assigned.assigned_to == "staff-1"
    assert assigned.assigned_at == now
    assert assigned.due_at == now + timedelta(hours=4)


def test_assign_escalation_is_idempotent_for_same_assignee() -> None:
    now = datetime(2026, 7, 1, 10, 0, tzinfo=UTC)
    later = now + timedelta(hours=1)
    repository = FakeHumanEscalationRepository()
    service = HumanEscalationService(
        repository=repository,
        clock=FixedClock(current_time=now),
    )
    escalation = service.create_or_get_active_escalation(
        conversation_id=uuid4(),
        reason=HumanEscalationReason.USER_REQUESTED_HUMAN,
    )
    first = service.assign_escalation(
        escalation.id,
        assigned_to="staff-1",
    )
    service = HumanEscalationService(
        repository=repository,
        clock=FixedClock(current_time=later),
    )
    second = service.assign_escalation(
        escalation.id,
        assigned_to="staff-1",
    )

    assert second.id == first.id
    assert second.assigned_at == now
    assert second.due_at == first.due_at
    assert second.updated_at == now


def test_assign_escalation_reassigns_to_different_staff() -> None:
    now = datetime(2026, 7, 1, 10, 0, tzinfo=UTC)
    later = now + timedelta(hours=2)
    repository = FakeHumanEscalationRepository()
    service = HumanEscalationService(
        repository=repository,
        clock=FixedClock(current_time=now),
    )
    escalation = service.create_or_get_active_escalation(
        conversation_id=uuid4(),
        reason=HumanEscalationReason.MEDICAL_EMERGENCY,
    )
    service.assign_escalation(
        escalation.id,
        assigned_to="staff-1",
    )
    service = HumanEscalationService(
        repository=repository,
        clock=FixedClock(current_time=later),
    )
    reassigned = service.assign_escalation(
        escalation.id,
        assigned_to="staff-2",
    )

    assert reassigned.assigned_to == "staff-2"
    assert reassigned.assigned_at == later
    assert reassigned.due_at == later + timedelta(minutes=15)


def test_assign_acknowledged_escalation_remains_acknowledged() -> None:
    now = datetime(2026, 7, 1, 10, 0, tzinfo=UTC)
    repository = FakeHumanEscalationRepository()
    service = HumanEscalationService(
        repository=repository,
        clock=FixedClock(current_time=now),
    )
    escalation = service.create_or_get_active_escalation(
        conversation_id=uuid4(),
        reason=HumanEscalationReason.NO_PROGRESS,
    )
    service.acknowledge_escalation(
        escalation.id,
        acknowledged_by="staff-1",
    )

    assigned = service.assign_escalation(
        escalation.id,
        assigned_to="staff-2",
    )

    assert assigned.status == HumanEscalationStatus.ACKNOWLEDGED
    assert assigned.assigned_to == "staff-2"


def test_assign_escalation_raises_not_found() -> None:
    service = HumanEscalationService(repository=FakeHumanEscalationRepository())

    with pytest.raises(APIError) as exc_info:
        service.assign_escalation(uuid4(), assigned_to="staff-1")

    assert exc_info.value.status_code == 404
    assert exc_info.value.code == "human_escalation_not_found"


def test_assign_resolved_escalation_raises_conflict() -> None:
    repository = FakeHumanEscalationRepository()
    service = HumanEscalationService(repository=repository)
    escalation = service.create_or_get_active_escalation(
        conversation_id=uuid4(),
        reason=HumanEscalationReason.USER_REQUESTED_HUMAN,
    )
    service.resolve_escalation(escalation.id, resolved_by="staff-1")

    with pytest.raises(APIError) as exc_info:
        service.assign_escalation(escalation.id, assigned_to="staff-2")

    assert exc_info.value.status_code == 409
    assert exc_info.value.code == "invalid_human_escalation_transition"


def test_assign_blank_assigned_to_raises_invalid_assignment() -> None:
    repository = FakeHumanEscalationRepository()
    service = HumanEscalationService(repository=repository)
    escalation = service.create_or_get_active_escalation(
        conversation_id=uuid4(),
        reason=HumanEscalationReason.USER_REQUESTED_HUMAN,
    )

    with pytest.raises(APIError) as exc_info:
        service.assign_escalation(escalation.id, assigned_to="   ")

    assert exc_info.value.status_code == 400
    assert exc_info.value.code == "invalid_human_escalation_assignment"


def test_unassign_escalation_clears_assignment_keeps_due_at() -> None:
    now = datetime(2026, 7, 1, 10, 0, tzinfo=UTC)
    later = now + timedelta(minutes=30)
    repository = FakeHumanEscalationRepository()
    service = HumanEscalationService(
        repository=repository,
        clock=FixedClock(current_time=now),
    )
    escalation = service.create_or_get_active_escalation(
        conversation_id=uuid4(),
        reason=HumanEscalationReason.USER_REQUESTED_HUMAN,
    )
    assigned = service.assign_escalation(
        escalation.id,
        assigned_to="staff-1",
    )
    service = HumanEscalationService(
        repository=repository,
        clock=FixedClock(current_time=later),
    )
    unassigned = service.unassign_escalation(escalation.id)

    assert unassigned.assigned_to is None
    assert unassigned.assigned_at is None
    assert unassigned.due_at == assigned.due_at
    assert unassigned.updated_at == later


def test_unassign_escalation_is_idempotent_when_unassigned() -> None:
    repository = FakeHumanEscalationRepository()
    service = HumanEscalationService(repository=repository)
    escalation = service.create_or_get_active_escalation(
        conversation_id=uuid4(),
        reason=HumanEscalationReason.USER_REQUESTED_HUMAN,
    )

    first = service.unassign_escalation(escalation.id)
    second = service.unassign_escalation(escalation.id)

    assert second.id == first.id
    assert second.assigned_to is None
    assert second.assigned_at is None


def test_unassign_resolved_escalation_raises_conflict() -> None:
    repository = FakeHumanEscalationRepository()
    service = HumanEscalationService(repository=repository)
    escalation = service.create_or_get_active_escalation(
        conversation_id=uuid4(),
        reason=HumanEscalationReason.USER_REQUESTED_HUMAN,
    )
    service.resolve_escalation(escalation.id, resolved_by="staff-1")

    with pytest.raises(APIError) as exc_info:
        service.unassign_escalation(escalation.id)

    assert exc_info.value.status_code == 409
    assert exc_info.value.code == "invalid_human_escalation_transition"


def test_list_escalations_filters_by_assigned_to() -> None:
    base_time = datetime(2026, 7, 1, 10, 0, tzinfo=UTC)
    assigned = HumanEscalation(
        id=uuid4(),
        conversation_id=uuid4(),
        status=HumanEscalationStatus.ACKNOWLEDGED,
        reason=HumanEscalationReason.USER_REQUESTED_HUMAN,
        priority=HumanEscalationPriority.HIGH,
        source=HumanEscalationSource.CHAT,
        assigned_to="staff-1",
        created_at=base_time,
        updated_at=base_time,
    )
    unassigned = HumanEscalation(
        id=uuid4(),
        conversation_id=uuid4(),
        status=HumanEscalationStatus.OPEN,
        reason=HumanEscalationReason.NO_PROGRESS,
        priority=HumanEscalationPriority.NORMAL,
        source=HumanEscalationSource.CHAT,
        created_at=base_time - timedelta(minutes=1),
        updated_at=base_time - timedelta(minutes=1),
    )
    service = HumanEscalationService(
        repository=FakeHumanEscalationRepository([assigned, unassigned]),
    )

    result = service.list_escalations(
        limit=10,
        filters=HumanEscalationListFilters(assigned_to="staff-1"),
    )

    assert len(result.items) == 1
    assert result.items[0].id == assigned.id


def test_list_escalations_filters_unassigned() -> None:
    base_time = datetime(2026, 7, 1, 10, 0, tzinfo=UTC)
    assigned = HumanEscalation(
        id=uuid4(),
        conversation_id=uuid4(),
        status=HumanEscalationStatus.ACKNOWLEDGED,
        reason=HumanEscalationReason.USER_REQUESTED_HUMAN,
        priority=HumanEscalationPriority.HIGH,
        source=HumanEscalationSource.CHAT,
        assigned_to="staff-1",
        created_at=base_time,
        updated_at=base_time,
    )
    unassigned = HumanEscalation(
        id=uuid4(),
        conversation_id=uuid4(),
        status=HumanEscalationStatus.OPEN,
        reason=HumanEscalationReason.NO_PROGRESS,
        priority=HumanEscalationPriority.NORMAL,
        source=HumanEscalationSource.CHAT,
        created_at=base_time - timedelta(minutes=1),
        updated_at=base_time - timedelta(minutes=1),
    )
    service = HumanEscalationService(
        repository=FakeHumanEscalationRepository([assigned, unassigned]),
    )

    result = service.list_escalations(
        limit=10,
        filters=HumanEscalationListFilters(unassigned=True),
    )

    assert len(result.items) == 1
    assert result.items[0].id == unassigned.id


def test_list_escalations_filters_overdue() -> None:
    now = datetime(2026, 7, 1, 10, 0, tzinfo=UTC)
    overdue = HumanEscalation(
        id=uuid4(),
        conversation_id=uuid4(),
        status=HumanEscalationStatus.OPEN,
        reason=HumanEscalationReason.USER_REQUESTED_HUMAN,
        priority=HumanEscalationPriority.HIGH,
        source=HumanEscalationSource.CHAT,
        due_at=now - timedelta(minutes=5),
        created_at=now - timedelta(hours=1),
        updated_at=now - timedelta(hours=1),
    )
    resolved_overdue = HumanEscalation(
        id=uuid4(),
        conversation_id=uuid4(),
        status=HumanEscalationStatus.RESOLVED,
        reason=HumanEscalationReason.NO_PROGRESS,
        priority=HumanEscalationPriority.NORMAL,
        source=HumanEscalationSource.CHAT,
        due_at=now - timedelta(minutes=10),
        created_at=now - timedelta(hours=2),
        updated_at=now - timedelta(minutes=1),
    )
    on_time = HumanEscalation(
        id=uuid4(),
        conversation_id=uuid4(),
        status=HumanEscalationStatus.ACKNOWLEDGED,
        reason=HumanEscalationReason.REPEATED_FALLBACK,
        priority=HumanEscalationPriority.NORMAL,
        source=HumanEscalationSource.CHAT,
        due_at=now + timedelta(hours=1),
        created_at=now - timedelta(minutes=30),
        updated_at=now - timedelta(minutes=30),
    )
    service = HumanEscalationService(
        repository=FakeHumanEscalationRepository(
            [overdue, resolved_overdue, on_time],
        ),
        clock=FixedClock(current_time=now),
    )

    overdue_result = service.list_escalations(
        limit=10,
        filters=HumanEscalationListFilters(overdue=True),
    )
    not_overdue_result = service.list_escalations(
        limit=10,
        filters=HumanEscalationListFilters(overdue=False),
    )

    assert [item.id for item in overdue_result.items] == [overdue.id]
    assert {item.id for item in not_overdue_result.items} == {
        resolved_overdue.id,
        on_time.id,
    }


def create_human_escalations(*, count: int) -> list[HumanEscalation]:
    base_time = datetime(2026, 7, 1, 10, 0, tzinfo=UTC)
    reasons = [
        HumanEscalationReason.USER_REQUESTED_HUMAN,
        HumanEscalationReason.REPEATED_FALLBACK,
        HumanEscalationReason.NO_PROGRESS,
    ]

    return [
        HumanEscalation(
            id=uuid4(),
            conversation_id=uuid4(),
            status=HumanEscalationStatus.OPEN,
            reason=reasons[index % len(reasons)],
            priority=HumanEscalationPriority.NORMAL,
            source=HumanEscalationSource.CHAT,
            created_at=base_time - timedelta(minutes=index),
            updated_at=base_time - timedelta(minutes=index),
        )
        for index in range(count)
    ]


class FakeHumanEscalationRepository:
    def __init__(self, escalations: Sequence[HumanEscalation] | None = None) -> None:
        self.escalations = list(escalations or [])

    def add(self, escalation: HumanEscalation) -> HumanEscalation:
        if escalation.id is None:
            escalation.id = uuid4()

        self.escalations.append(escalation)

        return escalation

    def get_by_id(self, escalation_id: UUID) -> HumanEscalation | None:
        for escalation in self.escalations:
            if escalation.id == escalation_id:
                return escalation

        return None

    def get_active_by_conversation_id(
        self,
        conversation_id: UUID,
    ) -> HumanEscalation | None:
        for escalation in self.escalations:
            if escalation.conversation_id != conversation_id:
                continue

            if escalation.status in {
                HumanEscalationStatus.OPEN,
                HumanEscalationStatus.ACKNOWLEDGED,
            }:
                return escalation

        return None

    def list(
        self,
        *,
        limit: int,
        cursor: HumanEscalationCursor | None = None,
        status: HumanEscalationStatus | None = None,
        reason: HumanEscalationReason | None = None,
        priority: HumanEscalationPriority | None = None,
        conversation_id: UUID | None = None,
        patient_id: UUID | None = None,
        appointment_id: UUID | None = None,
        assigned_to: str | None = None,
        unassigned: bool | None = None,
        overdue: bool | None = None,
        now: datetime | None = None,
    ) -> Sequence[HumanEscalation]:
        items = sorted(
            self.escalations,
            key=lambda item: (item.created_at, item.id),
            reverse=True,
        )

        if cursor is not None:
            items = [
                item
                for item in items
                if (item.created_at, item.id) < (cursor.created_at, cursor.id)
            ]

        if status is not None:
            items = [item for item in items if item.status == status]

        if reason is not None:
            items = [item for item in items if item.reason == reason]

        if priority is not None:
            items = [item for item in items if item.priority == priority]

        if conversation_id is not None:
            items = [
                item for item in items if item.conversation_id == conversation_id
            ]

        if patient_id is not None:
            items = [item for item in items if item.patient_id == patient_id]

        if appointment_id is not None:
            items = [
                item for item in items if item.appointment_id == appointment_id
            ]

        if assigned_to is not None:
            items = [item for item in items if item.assigned_to == assigned_to]

        if unassigned is True:
            items = [item for item in items if item.assigned_to is None]
        elif unassigned is False:
            items = [item for item in items if item.assigned_to is not None]

        if overdue is not None:
            if now is None:
                msg = "now is required when the overdue filter is set"
                raise ValueError(msg)

            active_statuses = {
                HumanEscalationStatus.OPEN,
                HumanEscalationStatus.ACKNOWLEDGED,
            }

            if overdue:
                items = [
                    item
                    for item in items
                    if item.due_at is not None
                    and item.due_at < now
                    and item.status in active_statuses
                ]
            else:
                items = [
                    item
                    for item in items
                    if item.due_at is None
                    or item.due_at >= now
                    or item.status not in active_statuses
                ]

        return items[:limit]

    def update(self, escalation: HumanEscalation) -> HumanEscalation:
        return escalation
