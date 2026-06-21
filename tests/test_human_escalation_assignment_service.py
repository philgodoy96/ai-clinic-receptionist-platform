from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import uuid4

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
from app.services.human_escalations import (
    HumanEscalationListFilters,
    HumanEscalationService,
    compute_due_at,
)
from tests.test_human_escalations import FakeHumanEscalationRepository

REFERENCE_NOW = datetime(2026, 7, 1, 10, 0, tzinfo=UTC)


def _service(
    *,
    repository: FakeHumanEscalationRepository | None = None,
    now: datetime = REFERENCE_NOW,
) -> HumanEscalationService:
    return HumanEscalationService(
        repository=repository or FakeHumanEscalationRepository(),
        clock=FixedClock(current_time=now),
    )


def _create_open_escalation(
    service: HumanEscalationService,
    *,
    reason: HumanEscalationReason = HumanEscalationReason.USER_REQUESTED_HUMAN,
) -> HumanEscalation:
    return service.create_or_get_active_escalation(
        conversation_id=uuid4(),
        reason=reason,
    )


def test_assign_open_escalation_sets_acknowledged_assignment_and_sla() -> None:
    service = _service()
    escalation = _create_open_escalation(service)

    assigned = service.assign_escalation(
        escalation.id,
        assigned_to="staff-1",
    )

    assert assigned.status == HumanEscalationStatus.ACKNOWLEDGED
    assert assigned.assigned_to == "staff-1"
    assert assigned.assigned_at == REFERENCE_NOW
    assert assigned.due_at == REFERENCE_NOW + timedelta(hours=4)


def test_assign_acknowledged_escalation_remains_acknowledged_and_sets_assignment() -> None:
    service = _service()
    escalation = _create_open_escalation(
        service,
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
    assert assigned.assigned_at == REFERENCE_NOW
    assert assigned.due_at == REFERENCE_NOW + timedelta(hours=24)


def test_urgent_due_at_is_now_plus_fifteen_minutes() -> None:
    service = _service()
    escalation = _create_open_escalation(
        service,
        reason=HumanEscalationReason.MEDICAL_EMERGENCY,
    )

    assigned = service.assign_escalation(
        escalation.id,
        assigned_to="staff-1",
    )

    assert assigned.due_at == REFERENCE_NOW + timedelta(minutes=15)
    assert compute_due_at(HumanEscalationPriority.URGENT, REFERENCE_NOW) == (
        REFERENCE_NOW + timedelta(minutes=15)
    )


def test_high_due_at_is_now_plus_four_hours() -> None:
    service = _service()
    escalation = _create_open_escalation(
        service,
        reason=HumanEscalationReason.USER_REQUESTED_HUMAN,
    )

    assigned = service.assign_escalation(
        escalation.id,
        assigned_to="staff-1",
    )

    assert assigned.due_at == REFERENCE_NOW + timedelta(hours=4)
    assert compute_due_at(HumanEscalationPriority.HIGH, REFERENCE_NOW) == (
        REFERENCE_NOW + timedelta(hours=4)
    )


def test_normal_due_at_is_now_plus_twenty_four_hours() -> None:
    service = _service()
    escalation = _create_open_escalation(
        service,
        reason=HumanEscalationReason.NO_PROGRESS,
    )

    assigned = service.assign_escalation(
        escalation.id,
        assigned_to="staff-1",
    )

    assert assigned.due_at == REFERENCE_NOW + timedelta(hours=24)
    assert compute_due_at(HumanEscalationPriority.NORMAL, REFERENCE_NOW) == (
        REFERENCE_NOW + timedelta(hours=24)
    )


def test_assigning_same_staff_twice_is_idempotent() -> None:
    repository = FakeHumanEscalationRepository()
    service = _service(repository=repository)
    escalation = _create_open_escalation(service)
    first = service.assign_escalation(
        escalation.id,
        assigned_to="staff-1",
    )

    later = REFERENCE_NOW + timedelta(hours=1)
    service = _service(repository=repository, now=later)
    second = service.assign_escalation(
        escalation.id,
        assigned_to="staff-1",
    )

    assert second.id == first.id
    assert second.assigned_to == "staff-1"
    assert second.assigned_at == REFERENCE_NOW
    assert second.due_at == first.due_at
    assert second.updated_at == REFERENCE_NOW


def test_assigning_different_staff_reassigns() -> None:
    repository = FakeHumanEscalationRepository()
    service = _service(repository=repository)
    escalation = _create_open_escalation(
        service,
        reason=HumanEscalationReason.MEDICAL_EMERGENCY,
    )
    service.assign_escalation(
        escalation.id,
        assigned_to="staff-1",
    )

    later = REFERENCE_NOW + timedelta(hours=2)
    service = _service(repository=repository, now=later)
    reassigned = service.assign_escalation(
        escalation.id,
        assigned_to="staff-2",
    )

    assert reassigned.assigned_to == "staff-2"
    assert reassigned.assigned_at == later
    assert reassigned.due_at == later + timedelta(minutes=15)


def test_unassign_clears_assignment_and_preserves_due_at() -> None:
    repository = FakeHumanEscalationRepository()
    service = _service(repository=repository)
    escalation = _create_open_escalation(service)
    assigned = service.assign_escalation(
        escalation.id,
        assigned_to="staff-1",
    )

    later = REFERENCE_NOW + timedelta(minutes=30)
    service = _service(repository=repository, now=later)
    unassigned = service.unassign_escalation(escalation.id)

    assert unassigned.assigned_to is None
    assert unassigned.assigned_at is None
    assert unassigned.due_at == assigned.due_at
    assert unassigned.updated_at == later


def test_assign_resolved_escalation_raises_invalid_transition() -> None:
    service = _service()
    escalation = _create_open_escalation(service)
    service.resolve_escalation(escalation.id, resolved_by="staff-1")

    with pytest.raises(APIError) as exc_info:
        service.assign_escalation(escalation.id, assigned_to="staff-2")

    assert exc_info.value.status_code == 409
    assert exc_info.value.code == "invalid_human_escalation_transition"


def test_unassign_resolved_escalation_raises_invalid_transition() -> None:
    service = _service()
    escalation = _create_open_escalation(service)
    service.resolve_escalation(escalation.id, resolved_by="staff-1")

    with pytest.raises(APIError) as exc_info:
        service.unassign_escalation(escalation.id)

    assert exc_info.value.status_code == 409
    assert exc_info.value.code == "invalid_human_escalation_transition"


def test_blank_assigned_to_raises_invalid_assignment_error() -> None:
    service = _service()
    escalation = _create_open_escalation(service)

    with pytest.raises(APIError) as exc_info:
        service.assign_escalation(escalation.id, assigned_to="   ")

    assert exc_info.value.status_code == 400
    assert exc_info.value.code == "invalid_human_escalation_assignment"


def test_list_assigned_to_returns_assigned_records_only() -> None:
    assigned = HumanEscalation(
        id=uuid4(),
        conversation_id=uuid4(),
        status=HumanEscalationStatus.ACKNOWLEDGED,
        reason=HumanEscalationReason.USER_REQUESTED_HUMAN,
        priority=HumanEscalationPriority.HIGH,
        source=HumanEscalationSource.CHAT,
        assigned_to="staff-1",
        created_at=REFERENCE_NOW,
        updated_at=REFERENCE_NOW,
    )
    unassigned = HumanEscalation(
        id=uuid4(),
        conversation_id=uuid4(),
        status=HumanEscalationStatus.OPEN,
        reason=HumanEscalationReason.NO_PROGRESS,
        priority=HumanEscalationPriority.NORMAL,
        source=HumanEscalationSource.CHAT,
        created_at=REFERENCE_NOW - timedelta(minutes=1),
        updated_at=REFERENCE_NOW - timedelta(minutes=1),
    )
    service = _service(
        repository=FakeHumanEscalationRepository([assigned, unassigned]),
    )

    result = service.list_escalations(
        limit=10,
        filters=HumanEscalationListFilters(assigned_to="staff-1"),
    )

    assert len(result.items) == 1
    assert result.items[0].id == assigned.id
    assert result.items[0].assigned_to == "staff-1"


def test_list_unassigned_returns_open_and_acknowledged_without_assignment() -> None:
    open_unassigned = HumanEscalation(
        id=uuid4(),
        conversation_id=uuid4(),
        status=HumanEscalationStatus.OPEN,
        reason=HumanEscalationReason.NO_PROGRESS,
        priority=HumanEscalationPriority.NORMAL,
        source=HumanEscalationSource.CHAT,
        created_at=REFERENCE_NOW,
        updated_at=REFERENCE_NOW,
    )
    acknowledged_unassigned = HumanEscalation(
        id=uuid4(),
        conversation_id=uuid4(),
        status=HumanEscalationStatus.ACKNOWLEDGED,
        reason=HumanEscalationReason.REPEATED_FALLBACK,
        priority=HumanEscalationPriority.NORMAL,
        source=HumanEscalationSource.CHAT,
        created_at=REFERENCE_NOW - timedelta(minutes=1),
        updated_at=REFERENCE_NOW - timedelta(minutes=1),
    )
    assigned = HumanEscalation(
        id=uuid4(),
        conversation_id=uuid4(),
        status=HumanEscalationStatus.ACKNOWLEDGED,
        reason=HumanEscalationReason.USER_REQUESTED_HUMAN,
        priority=HumanEscalationPriority.HIGH,
        source=HumanEscalationSource.CHAT,
        assigned_to="staff-1",
        created_at=REFERENCE_NOW - timedelta(minutes=2),
        updated_at=REFERENCE_NOW - timedelta(minutes=2),
    )
    service = _service(
        repository=FakeHumanEscalationRepository(
            [open_unassigned, acknowledged_unassigned, assigned],
        ),
    )

    result = service.list_escalations(
        limit=10,
        filters=HumanEscalationListFilters(unassigned=True),
    )

    assert {item.id for item in result.items} == {
        open_unassigned.id,
        acknowledged_unassigned.id,
    }
    assert all(item.assigned_to is None for item in result.items)
    assert all(
        item.status in {
            HumanEscalationStatus.OPEN,
            HumanEscalationStatus.ACKNOWLEDGED,
        }
        for item in result.items
    )


def test_list_overdue_returns_active_records_past_due_at_only() -> None:
    overdue = HumanEscalation(
        id=uuid4(),
        conversation_id=uuid4(),
        status=HumanEscalationStatus.OPEN,
        reason=HumanEscalationReason.USER_REQUESTED_HUMAN,
        priority=HumanEscalationPriority.HIGH,
        source=HumanEscalationSource.CHAT,
        due_at=REFERENCE_NOW - timedelta(minutes=5),
        created_at=REFERENCE_NOW - timedelta(hours=1),
        updated_at=REFERENCE_NOW - timedelta(hours=1),
    )
    on_time = HumanEscalation(
        id=uuid4(),
        conversation_id=uuid4(),
        status=HumanEscalationStatus.ACKNOWLEDGED,
        reason=HumanEscalationReason.REPEATED_FALLBACK,
        priority=HumanEscalationPriority.NORMAL,
        source=HumanEscalationSource.CHAT,
        due_at=REFERENCE_NOW + timedelta(hours=1),
        created_at=REFERENCE_NOW - timedelta(minutes=30),
        updated_at=REFERENCE_NOW - timedelta(minutes=30),
    )
    service = _service(
        repository=FakeHumanEscalationRepository([overdue, on_time]),
    )

    result = service.list_escalations(
        limit=10,
        filters=HumanEscalationListFilters(overdue=True),
    )

    assert [item.id for item in result.items] == [overdue.id]
    assert result.items[0].due_at is not None
    assert result.items[0].due_at < REFERENCE_NOW


def test_list_overdue_excludes_resolved_record_even_when_past_due_at() -> None:
    resolved_overdue = HumanEscalation(
        id=uuid4(),
        conversation_id=uuid4(),
        status=HumanEscalationStatus.RESOLVED,
        reason=HumanEscalationReason.NO_PROGRESS,
        priority=HumanEscalationPriority.NORMAL,
        source=HumanEscalationSource.CHAT,
        due_at=REFERENCE_NOW - timedelta(minutes=10),
        created_at=REFERENCE_NOW - timedelta(hours=2),
        updated_at=REFERENCE_NOW - timedelta(minutes=1),
    )
    active_overdue = HumanEscalation(
        id=uuid4(),
        conversation_id=uuid4(),
        status=HumanEscalationStatus.ACKNOWLEDGED,
        reason=HumanEscalationReason.MEDICAL_EMERGENCY,
        priority=HumanEscalationPriority.URGENT,
        source=HumanEscalationSource.CHAT,
        due_at=REFERENCE_NOW - timedelta(minutes=1),
        created_at=REFERENCE_NOW - timedelta(hours=1),
        updated_at=REFERENCE_NOW - timedelta(minutes=30),
    )
    service = _service(
        repository=FakeHumanEscalationRepository(
            [resolved_overdue, active_overdue],
        ),
    )

    overdue_result = service.list_escalations(
        limit=10,
        filters=HumanEscalationListFilters(overdue=True),
    )
    not_overdue_result = service.list_escalations(
        limit=10,
        filters=HumanEscalationListFilters(overdue=False),
    )

    assert [item.id for item in overdue_result.items] == [active_overdue.id]
    assert resolved_overdue.id in {item.id for item in not_overdue_result.items}
