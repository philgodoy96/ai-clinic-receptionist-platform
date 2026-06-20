from datetime import datetime
from typing import Protocol
from uuid import UUID

from sqlalchemy.orm import Session

from app.domain.audit.enums import AuditActorType, AuditEventOutcome, AuditEventType
from app.domain.scheduling.appointment_holds import AppointmentHold
from app.models.scheduling import AvailabilitySlot
from app.schemas.retell_tools import RetellHoldAppointmentSlotRequest, RetellToolResponse
from app.services.appointment_holds import (
    AppointmentSlotAlreadyHeldError,
    InvalidAppointmentHoldOwnerError,
    InvalidAppointmentHoldWindowError,
)
from app.services.audit_logs import AuditLogCreate, AuditLogService
from app.services.scheduling import (
    AvailabilitySlotNotFoundError,
    AvailabilitySlotUnavailableError,
)

RETELL_TOOL_SOURCE = "retell_tool"


class SchedulingServiceForRetellHolds(Protocol):
    def get_available_slot_for_hold(self, availability_slot_id: UUID) -> AvailabilitySlot:
        raise NotImplementedError


class AppointmentHoldServiceForRetell(Protocol):
    ttl_seconds: int

    def create_hold(
        self,
        *,
        availability_slot_id: UUID,
        doctor_id: UUID,
        start_time: datetime,
        end_time: datetime,
        owner_id: str,
    ) -> AppointmentHold:
        raise NotImplementedError


class RetellAppointmentHoldToolAdapter:
    def __init__(
        self,
        *,
        db: Session,
        scheduling_service: SchedulingServiceForRetellHolds,
        hold_service: AppointmentHoldServiceForRetell,
        audit_logs: AuditLogService,
    ) -> None:
        self.db = db
        self.scheduling_service = scheduling_service
        self.hold_service = hold_service
        self.audit_logs = audit_logs

    def hold_appointment_slot(
        self,
        payload: RetellHoldAppointmentSlotRequest,
    ) -> RetellToolResponse:
        owner_id = self._resolve_owner_id(payload)

        if owner_id is None:
            return RetellToolResponse(
                ok=False,
                error_code="missing_hold_owner",
                message="A call_id, conversation_id, or owner_id is required.",
            )

        try:
            slot = self.scheduling_service.get_available_slot_for_hold(
                payload.availability_slot_id,
            )
            hold = self.hold_service.create_hold(
                availability_slot_id=slot.id,
                doctor_id=slot.doctor_id,
                start_time=slot.start_time,
                end_time=slot.end_time,
                owner_id=owner_id,
            )
        except AvailabilitySlotNotFoundError:
            self._record_hold_failure(
                owner_id=owner_id,
                payload=payload,
                reason="availability_slot_not_found",
            )
            return RetellToolResponse(
                ok=False,
                error_code="availability_slot_not_found",
                message="The selected availability slot was not found.",
            )
        except AvailabilitySlotUnavailableError:
            self._record_hold_failure(
                owner_id=owner_id,
                payload=payload,
                reason="availability_slot_unavailable",
            )
            return RetellToolResponse(
                ok=False,
                error_code="availability_slot_unavailable",
                message="The selected availability slot is not available.",
            )
        except AppointmentSlotAlreadyHeldError:
            self._record_hold_failure(
                owner_id=owner_id,
                payload=payload,
                reason="slot_already_held",
            )
            return RetellToolResponse(
                ok=False,
                error_code="slot_already_held",
                message="The selected slot is already being held.",
            )
        except (InvalidAppointmentHoldOwnerError, InvalidAppointmentHoldWindowError):
            self._record_hold_failure(
                owner_id=owner_id,
                payload=payload,
                reason="invalid_appointment_hold",
            )
            return RetellToolResponse(
                ok=False,
                error_code="invalid_appointment_hold",
                message="The appointment hold request is invalid.",
            )

        self._commit_audit_best_effort(
            AuditLogCreate(
                event_type=AuditEventType.APPOINTMENT_HOLD_CREATED,
                outcome=AuditEventOutcome.SUCCESS,
                actor_type=AuditActorType.RETELL,
                source=RETELL_TOOL_SOURCE,
                actor_id=owner_id,
                call_id=payload.call_id,
                conversation_id=payload.conversation_id,
                availability_slot_id=hold.availability_slot_id,
                event_metadata={
                    "hold_id": str(hold.hold_id),
                    "doctor_id": str(hold.doctor_id),
                },
            ),
        )

        return RetellToolResponse(
            ok=True,
            result={
                "hold_id": str(hold.hold_id),
                "availability_slot_id": str(hold.availability_slot_id),
                "doctor_id": str(hold.doctor_id),
                "start_time": hold.start_time.isoformat(),
                "end_time": hold.end_time.isoformat(),
                "expires_in_seconds": self.hold_service.ttl_seconds,
            },
        )

    def _record_hold_failure(
        self,
        *,
        owner_id: str,
        payload: RetellHoldAppointmentSlotRequest,
        reason: str,
    ) -> None:
        self._commit_audit_best_effort(
            AuditLogCreate(
                event_type=AuditEventType.APPOINTMENT_HOLD_FAILED,
                outcome=AuditEventOutcome.FAILURE,
                actor_type=AuditActorType.RETELL,
                source=RETELL_TOOL_SOURCE,
                actor_id=owner_id,
                call_id=payload.call_id,
                conversation_id=payload.conversation_id,
                availability_slot_id=payload.availability_slot_id,
                event_metadata={"reason": reason},
            ),
        )

    def _commit_audit_best_effort(self, payload: AuditLogCreate) -> None:
        try:
            self.audit_logs.record(payload)
            self.db.commit()
        except Exception:
            try:
                self.db.rollback()
            except Exception:
                return

    def _resolve_owner_id(self, payload: RetellHoldAppointmentSlotRequest) -> str | None:
        for candidate in [payload.owner_id, payload.call_id, payload.conversation_id]:
            if candidate is None:
                continue

            normalized = candidate.strip()

            if normalized:
                return normalized

        return None
