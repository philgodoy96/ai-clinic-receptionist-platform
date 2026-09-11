from collections.abc import Sequence
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from sqlalchemy import and_, asc, desc, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.domain.voice_calls.enums import VoiceCallStatus
from app.domain.voice_tool_execution import (
    ToolExecutionClaimResult,
    ToolExecutionStaleReclaimPolicy,
    VoiceToolExecutionStatus,
    build_failed_execution_metadata,
    build_in_progress_execution_metadata,
    build_succeeded_execution_metadata,
    is_stale_in_progress_claim,
    mark_ambiguous_dual_write_recovery,
    read_tool_execution_claimed_at,
    read_tool_execution_outcome,
    read_tool_execution_status,
)
from app.models.voice_calls import VoiceCall, VoiceCallEvent
from app.services.voice_call_pagination import VoiceCallCursor, VoiceCallEventCursor


class SQLAlchemyVoiceCallRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def get_by_id(self, voice_call_id: UUID) -> VoiceCall | None:
        return self.session.get(VoiceCall, voice_call_id)

    def get_by_provider_call_id(
        self,
        *,
        provider: str,
        provider_call_id: str,
    ) -> VoiceCall | None:
        statement = select(VoiceCall).where(
            VoiceCall.provider == provider,
            VoiceCall.provider_call_id == provider_call_id,
        )

        return self.session.scalars(statement).first()

    def create_voice_call(self, voice_call: VoiceCall) -> VoiceCall:
        self.session.add(voice_call)
        self.session.flush()

        return voice_call

    def get_or_create_voice_call(
        self,
        *,
        provider: str,
        provider_call_id: str,
    ) -> tuple[VoiceCall, bool]:
        existing = self.get_by_provider_call_id(
            provider=provider,
            provider_call_id=provider_call_id,
        )
        if existing is not None:
            return existing, False

        voice_call = VoiceCall(
            provider=provider,
            provider_call_id=provider_call_id,
        )

        try:
            return self.create_voice_call(voice_call), True
        except IntegrityError:
            self.session.rollback()
            raced = self.get_by_provider_call_id(
                provider=provider,
                provider_call_id=provider_call_id,
            )
            if raced is None:
                raise

            return raced, False

    def update_voice_call(self, voice_call: VoiceCall) -> VoiceCall:
        self.session.add(voice_call)
        self.session.flush()

        return voice_call

    def create_voice_call_event(self, voice_call_event: VoiceCallEvent) -> VoiceCallEvent:
        self.session.add(voice_call_event)
        self.session.flush()

        return voice_call_event

    def get_event_by_idempotency_key(
        self,
        *,
        idempotency_key: str,
    ) -> VoiceCallEvent | None:
        statement = (
            select(VoiceCallEvent).where(VoiceCallEvent.idempotency_key == idempotency_key).limit(1)
        )

        return self.session.scalars(statement).first()

    def get_tool_call_outcome_by_idempotency_key(
        self,
        *,
        idempotency_key: str,
    ) -> dict[str, Any] | None:
        event = self.get_event_by_idempotency_key(idempotency_key=idempotency_key)

        if event is None:
            return None

        status = read_tool_execution_status(event.event_metadata)
        if status is VoiceToolExecutionStatus.IN_PROGRESS:
            return None
        if status is VoiceToolExecutionStatus.FAILED:
            return None

        return read_tool_execution_outcome(event.event_metadata)

    def claim_tool_call_execution(
        self,
        *,
        voice_call_id: UUID,
        provider: str,
        provider_call_id: str,
        event_type: str,
        tool_call_id: str,
        idempotency_key: str,
        occurred_at: datetime,
        stale_reclaim_policy: ToolExecutionStaleReclaimPolicy = (
            ToolExecutionStaleReclaimPolicy.REQUIRE_MANUAL_RECOVERY
        ),
    ) -> ToolExecutionClaimResult:
        existing = self._lock_event_by_idempotency_key(idempotency_key=idempotency_key)
        if existing is not None:
            return self._resolve_existing_claim(
                existing,
                stale_reclaim_policy=stale_reclaim_policy,
            )

        claimed_at = datetime.now(UTC)
        voice_call_event = VoiceCallEvent(
            voice_call_id=voice_call_id,
            provider=provider,
            provider_call_id=provider_call_id,
            provider_event_id=tool_call_id,
            event_type=event_type,
            occurred_at=occurred_at,
            event_metadata=build_in_progress_execution_metadata(claimed_at=claimed_at),
            idempotency_key=idempotency_key,
        )

        try:
            self.create_voice_call_event(voice_call_event)
        except IntegrityError:
            self.session.rollback()
            raced = self._lock_event_by_idempotency_key(idempotency_key=idempotency_key)
            if raced is None:
                raced = self.get_event_by_idempotency_key(idempotency_key=idempotency_key)
            if raced is None:
                raise
            return self._resolve_existing_claim(
                raced,
                stale_reclaim_policy=stale_reclaim_policy,
            )

        return ToolExecutionClaimResult(
            owned=True,
            status=VoiceToolExecutionStatus.IN_PROGRESS,
            outcome=None,
        )

    def complete_tool_call_execution(
        self,
        *,
        idempotency_key: str,
        outcome: dict[str, Any],
    ) -> bool:
        event = self._lock_event_by_idempotency_key(idempotency_key=idempotency_key)
        if event is None:
            return False

        claimed_at = read_tool_execution_claimed_at(event.event_metadata)
        event.event_metadata = build_succeeded_execution_metadata(
            outcome,
            claimed_at=claimed_at,
        )
        self.session.add(event)
        self.session.flush()
        return True

    def fail_tool_call_execution(
        self,
        *,
        idempotency_key: str,
        error_code: str | None = None,
    ) -> bool:
        event = self._lock_event_by_idempotency_key(idempotency_key=idempotency_key)
        if event is None:
            return False

        status = read_tool_execution_status(event.event_metadata)
        if status is VoiceToolExecutionStatus.SUCCEEDED:
            return False

        claimed_at = read_tool_execution_claimed_at(event.event_metadata)
        event.event_metadata = build_failed_execution_metadata(
            error_code=error_code,
            claimed_at=claimed_at,
        )
        self.session.add(event)
        self.session.flush()
        return True

    def record_tool_call_outcome(
        self,
        *,
        voice_call_id: UUID,
        provider: str,
        provider_call_id: str,
        event_type: str,
        tool_call_id: str,
        idempotency_key: str,
        outcome: dict[str, Any],
        occurred_at: datetime,
    ) -> bool:
        existing = self.get_event_by_idempotency_key(idempotency_key=idempotency_key)

        if existing is not None:
            status = read_tool_execution_status(existing.event_metadata)
            if status is VoiceToolExecutionStatus.SUCCEEDED and read_tool_execution_outcome(
                existing.event_metadata,
            ):
                return False
            return self.complete_tool_call_execution(
                idempotency_key=idempotency_key,
                outcome=outcome,
            )

        voice_call_event = VoiceCallEvent(
            voice_call_id=voice_call_id,
            provider=provider,
            provider_call_id=provider_call_id,
            provider_event_id=tool_call_id,
            event_type=event_type,
            occurred_at=occurred_at,
            event_metadata=build_succeeded_execution_metadata(outcome),
            idempotency_key=idempotency_key,
        )

        try:
            self.create_voice_call_event(voice_call_event)
        except IntegrityError:
            self.session.rollback()
            return False

        return True

    def _lock_event_by_idempotency_key(
        self,
        *,
        idempotency_key: str,
    ) -> VoiceCallEvent | None:
        statement = (
            select(VoiceCallEvent)
            .where(VoiceCallEvent.idempotency_key == idempotency_key)
            .limit(1)
            .with_for_update()
        )
        return self.session.scalars(statement).first()

    def _resolve_existing_claim(
        self,
        event: VoiceCallEvent,
        *,
        stale_reclaim_policy: ToolExecutionStaleReclaimPolicy,
    ) -> ToolExecutionClaimResult:
        status = read_tool_execution_status(event.event_metadata)
        outcome = read_tool_execution_outcome(event.event_metadata)

        if status is VoiceToolExecutionStatus.SUCCEEDED or (
            status is None and outcome is not None
        ):
            return ToolExecutionClaimResult(
                owned=False,
                status=VoiceToolExecutionStatus.SUCCEEDED,
                outcome=outcome,
            )

        # Explicit FAILED is a deliberate retry signal from a completed failure path.
        if status is VoiceToolExecutionStatus.FAILED:
            reclaimed = self._reclaim_execution(event)
            if reclaimed:
                return ToolExecutionClaimResult(
                    owned=True,
                    status=VoiceToolExecutionStatus.IN_PROGRESS,
                    outcome=None,
                )

        if is_stale_in_progress_claim(event.event_metadata):
            if stale_reclaim_policy is ToolExecutionStaleReclaimPolicy.ALLOW_SAFE_RETRY:
                reclaimed = self._reclaim_execution(event)
                if reclaimed:
                    return ToolExecutionClaimResult(
                        owned=True,
                        status=VoiceToolExecutionStatus.IN_PROGRESS,
                        outcome=None,
                    )
            else:
                event.event_metadata = mark_ambiguous_dual_write_recovery(event.event_metadata)
                self.session.add(event)
                self.session.flush()
                return ToolExecutionClaimResult(
                    owned=False,
                    status=VoiceToolExecutionStatus.IN_PROGRESS,
                    outcome=None,
                    recovery_required=True,
                )

        # Legacy rows without an execution status and without an outcome.
        if status is None:
            if stale_reclaim_policy is ToolExecutionStaleReclaimPolicy.ALLOW_SAFE_RETRY:
                reclaimed = self._reclaim_execution(event)
                if reclaimed:
                    return ToolExecutionClaimResult(
                        owned=True,
                        status=VoiceToolExecutionStatus.IN_PROGRESS,
                        outcome=None,
                    )
            return ToolExecutionClaimResult(
                owned=False,
                status=VoiceToolExecutionStatus.IN_PROGRESS,
                outcome=None,
                recovery_required=True,
            )

        return ToolExecutionClaimResult(
            owned=False,
            status=status or VoiceToolExecutionStatus.IN_PROGRESS,
            outcome=outcome,
        )

    def _reclaim_execution(self, event: VoiceCallEvent) -> bool:
        status = read_tool_execution_status(event.event_metadata)
        if status is VoiceToolExecutionStatus.SUCCEEDED or (
            status is None and read_tool_execution_outcome(event.event_metadata) is not None
        ):
            return False

        if status is VoiceToolExecutionStatus.IN_PROGRESS and not is_stale_in_progress_claim(
            event.event_metadata,
        ):
            return False

        event.event_metadata = build_in_progress_execution_metadata()
        self.session.add(event)
        self.session.flush()
        return True

    def list_voice_calls(
        self,
        *,
        limit: int,
        cursor: VoiceCallCursor | None = None,
        status: VoiceCallStatus | None = None,
        provider: str | None = None,
        provider_call_id: str | None = None,
        created_after: datetime | None = None,
    ) -> Sequence[VoiceCall]:
        statement = select(VoiceCall)

        if cursor is not None:
            statement = statement.where(
                or_(
                    VoiceCall.created_at < cursor.created_at,
                    and_(
                        VoiceCall.created_at == cursor.created_at,
                        VoiceCall.id < cursor.id,
                    ),
                ),
            )

        if status is not None:
            statement = statement.where(VoiceCall.status == status)

        if provider is not None:
            statement = statement.where(VoiceCall.provider == provider)

        if provider_call_id is not None:
            statement = statement.where(VoiceCall.provider_call_id == provider_call_id)

        if created_after is not None:
            statement = statement.where(VoiceCall.created_at >= created_after)

        statement = statement.order_by(
            desc(VoiceCall.created_at),
            desc(VoiceCall.id),
        ).limit(limit)

        return list(self.session.scalars(statement).all())

    def list_events_for_call(
        self,
        *,
        voice_call_id: UUID,
        limit: int,
        cursor: VoiceCallEventCursor | None = None,
    ) -> Sequence[VoiceCallEvent]:
        statement = select(VoiceCallEvent).where(
            VoiceCallEvent.voice_call_id == voice_call_id,
        )

        if cursor is not None:
            statement = statement.where(
                or_(
                    VoiceCallEvent.occurred_at > cursor.occurred_at,
                    and_(
                        VoiceCallEvent.occurred_at == cursor.occurred_at,
                        VoiceCallEvent.id > cursor.id,
                    ),
                ),
            )

        statement = statement.order_by(
            asc(VoiceCallEvent.occurred_at),
            asc(VoiceCallEvent.id),
        ).limit(limit)

        return list(self.session.scalars(statement).all())
