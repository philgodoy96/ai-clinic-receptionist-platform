from __future__ import annotations

from typing import Protocol

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models.scheduling import Appointment
from app.schemas.retell_tools import RetellBookAppointmentRequest, RetellToolResponse
from app.schemas.scheduling import AppointmentResponse
from app.services.appointment_booking import (
    AppointmentBookingOwnerRequiredError,
    AppointmentBookingRequest,
    AppointmentBookingResult,
    AppointmentSlotAlreadyBookedError,
    BookingAvailabilitySlotNotFoundError,
    BookingAvailabilitySlotUnavailableError,
    BookingDoctorNotFoundError,
    BookingPatientNotFoundError,
)
from app.services.appointment_holds import (
    AppointmentHoldMismatchError,
    AppointmentHoldNotFoundError,
    AppointmentHoldOwnershipError,
    AppointmentHoldService,
)


class AppointmentBookingServiceForRetell(Protocol):
    def book_appointment(self, request: AppointmentBookingRequest) -> AppointmentBookingResult:
        raise NotImplementedError


class RetellAppointmentBookingToolAdapter:
    def __init__(
        self,
        *,
        db: Session,
        booking_service: AppointmentBookingServiceForRetell,
        hold_service: AppointmentHoldService,
    ) -> None:
        self.db = db
        self.booking_service = booking_service
        self.hold_service = hold_service

    def book_appointment(
        self,
        payload: RetellBookAppointmentRequest,
    ) -> RetellToolResponse:
        owner_id = self._resolve_owner_id(payload)

        if owner_id is None:
            return RetellToolResponse(
                ok=False,
                error_code="missing_booking_owner",
                message="A call_id, conversation_id, or owner_id is required.",
            )

        try:
            booking_result = self.booking_service.book_appointment(
                AppointmentBookingRequest(
                    hold_id=payload.hold_id,
                    availability_slot_id=payload.availability_slot_id,
                    patient_id=payload.patient_id,
                    owner_id=owner_id,
                    reason=payload.reason,
                ),
            )
            appointment = booking_result.appointment
            hold = booking_result.hold

            self.db.commit()
            self.db.refresh(appointment)

            self.hold_service.release_hold(
                doctor_id=hold.doctor_id,
                start_time=hold.start_time,
                owner_id=owner_id,
            )
        except IntegrityError:
            self.db.rollback()
            return self._error(
                "appointment_conflict",
                "The selected slot is no longer available.",
            )
        except BookingPatientNotFoundError:
            self.db.rollback()
            return self._error("patient_not_found", "The patient was not found.")
        except BookingDoctorNotFoundError:
            self.db.rollback()
            return self._error(
                "doctor_not_found",
                "The doctor was not found or is inactive.",
            )
        except BookingAvailabilitySlotNotFoundError:
            self.db.rollback()
            return self._error(
                "availability_slot_not_found",
                "The selected availability slot was not found.",
            )
        except BookingAvailabilitySlotUnavailableError:
            self.db.rollback()
            return self._error(
                "availability_slot_unavailable",
                "The selected availability slot is not available.",
            )
        except AppointmentSlotAlreadyBookedError:
            self.db.rollback()
            return self._error(
                "slot_already_booked",
                "The selected slot is already booked.",
            )
        except AppointmentBookingOwnerRequiredError:
            self.db.rollback()
            return self._error(
                "missing_booking_owner",
                "A call_id, conversation_id, or owner_id is required.",
            )
        except AppointmentHoldNotFoundError:
            self.db.rollback()
            return self._error(
                "appointment_hold_expired",
                "The temporary hold was not found or has expired.",
            )
        except AppointmentHoldMismatchError:
            self.db.rollback()
            return self._error(
                "appointment_hold_mismatch",
                "The hold ID does not match the selected slot.",
            )
        except AppointmentHoldOwnershipError:
            self.db.rollback()
            return self._error(
                "appointment_hold_owner_mismatch",
                "The appointment hold belongs to another caller.",
            )

        return RetellToolResponse(
            ok=True,
            result={
                "appointment": self._appointment_to_result(appointment),
            },
        )

    def _resolve_owner_id(self, payload: RetellBookAppointmentRequest) -> str | None:
        for candidate in [payload.owner_id, payload.call_id, payload.conversation_id]:
            if candidate is None:
                continue

            normalized = candidate.strip()

            if normalized:
                return normalized

        return None

    def _appointment_to_result(self, appointment: Appointment) -> dict[str, object]:
        return AppointmentResponse.model_validate(appointment).model_dump(mode="json")

    def _error(self, error_code: str, message: str) -> RetellToolResponse:
        return RetellToolResponse(
            ok=False,
            error_code=error_code,
            message=message,
        )