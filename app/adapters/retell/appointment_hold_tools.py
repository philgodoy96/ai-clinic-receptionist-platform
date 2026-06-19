from datetime import datetime
from typing import Protocol
from uuid import UUID

from app.domain.scheduling.appointment_holds import AppointmentHold
from app.models.scheduling import AvailabilitySlot
from app.schemas.retell_tools import RetellHoldAppointmentSlotRequest, RetellToolResponse
from app.services.appointment_holds import (
    AppointmentSlotAlreadyHeldError,
    InvalidAppointmentHoldOwnerError,
    InvalidAppointmentHoldWindowError,
)
from app.services.scheduling import (
    AvailabilitySlotNotFoundError,
    AvailabilitySlotUnavailableError,
)


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
        scheduling_service: SchedulingServiceForRetellHolds,
        hold_service: AppointmentHoldServiceForRetell,
    ) -> None:
        self.scheduling_service = scheduling_service
        self.hold_service = hold_service

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
            return RetellToolResponse(
                ok=False,
                error_code="availability_slot_not_found",
                message="The selected availability slot was not found.",
            )
        except AvailabilitySlotUnavailableError:
            return RetellToolResponse(
                ok=False,
                error_code="availability_slot_unavailable",
                message="The selected availability slot is not available.",
            )
        except AppointmentSlotAlreadyHeldError:
            return RetellToolResponse(
                ok=False,
                error_code="slot_already_held",
                message="The selected slot is already being held.",
            )
        except (InvalidAppointmentHoldOwnerError, InvalidAppointmentHoldWindowError):
            return RetellToolResponse(
                ok=False,
                error_code="invalid_appointment_hold",
                message="The appointment hold request is invalid.",
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

    def _resolve_owner_id(self, payload: RetellHoldAppointmentSlotRequest) -> str | None:
        for candidate in [payload.owner_id, payload.call_id, payload.conversation_id]:
            if candidate is None:
                continue

            normalized = candidate.strip()

            if normalized:
                return normalized

        return None