from collections.abc import Sequence
from typing import Protocol
from uuid import UUID

from app.domain.human_escalations import (
    HumanEscalationPriority,
    HumanEscalationReason,
    HumanEscalationStatus,
)
from app.models.human_escalation import HumanEscalation
from app.services.human_escalation_pagination import HumanEscalationCursor


class HumanEscalationRepository(Protocol):
    def add(self, escalation: HumanEscalation) -> HumanEscalation:
        raise NotImplementedError

    def get_by_id(self, escalation_id: UUID) -> HumanEscalation | None:
        raise NotImplementedError

    def get_active_by_conversation_id(
        self,
        conversation_id: UUID,
    ) -> HumanEscalation | None:
        raise NotImplementedError

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
    ) -> Sequence[HumanEscalation]:
        raise NotImplementedError

    def update(self, escalation: HumanEscalation) -> HumanEscalation:
        raise NotImplementedError
