from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy.exc import IntegrityError

from app.domain.appointments import (
    APPOINTMENT_CANCELLATION_SOURCE,
    AppointmentCancellationMissingConfirmationError,
    AppointmentCancellationRequest,
    AppointmentCancellationResult,
    AppointmentNotCancelableError,
    AppointmentNotFoundError,
    is_appointment_cancelable,
)
from app.domain.audit.enums import AuditEventOutcome, AuditEventType
from app.domain.scheduling.enums import AppointmentStatus, AvailabilitySlotStatus
from app.models.appointment_cancellation_attempt import AppointmentCancellationAttempt
from app.models.scheduling import Appointment
from app.repositories.appointments import (
    AppointmentCancellationAttemptRepository,
    AppointmentRepository,
)
from app.repositories.scheduling import AvailabilitySlotRepository
from app.services.audit_logs import AuditLogCreate, AuditLogService


class AppointmentCancellationInProgressError(Exception):
    """Raised when another worker already owns this cancellation execution identity."""

    def __init__(
        self,
        message: str = "A cancellation for this tool call is already in progress.",
    ) -> None:
        self.message = message
        super().__init__(message)


class AppointmentCancellationService:
    def __init__(
        self,
        *,
        appointments: AppointmentRepository,
        cancellation_attempts: AppointmentCancellationAttemptRepository,
        audit_logs: AuditLogService,
        availability_slots: AvailabilitySlotRepository | None = None,
    ) -> None:
        self.appointments = appointments
        self.cancellation_attempts = cancellation_attempts
        self.audit_logs = audit_logs
        self.availability_slots = availability_slots

    def cancel_appointment(
        self,
        request: AppointmentCancellationRequest,
    ) -> AppointmentCancellationResult:
        if not request.explicit_confirmation:
            msg = "explicit_confirmation is required"
            raise AppointmentCancellationMissingConfirmationError(msg)

        existing_attempt = self.cancellation_attempts.get_by_idempotency_key(
            request.idempotency_key,
        )
        if existing_attempt is not None:
            completed = self._completed_result_from_existing_attempt(
                existing_attempt,
                duplicate=True,
            )
            if completed is not None:
                return completed
            # Incomplete claim after a crash: finish the local mutation.
            appointment = self.appointments.get_by_id(existing_attempt.appointment_id)
            if appointment is None:
                msg = "appointment was not found"
                raise AppointmentNotFoundError(msg)
            return self._mutate_cancelled(request, appointment, duplicate=False)

        appointment = self.appointments.get_by_id(request.appointment_id)
        if appointment is None:
            msg = "appointment was not found"
            raise AppointmentNotFoundError(msg)

        if appointment.status == AppointmentStatus.CANCELLED:
            self._record_attempt_best_effort(request, appointment.id)
            return AppointmentCancellationResult(
                appointment_id=appointment.id,
                patient_id=appointment.patient_id,
                already_cancelled=True,
                cancelled_at=appointment.cancelled_at,
            )

        if not is_appointment_cancelable(appointment.status):
            msg = "appointment cannot be cancelled"
            raise AppointmentNotCancelableError(msg)

        claim = self._claim_execution(request, appointment.id)
        if claim is None:
            existing_after_race = self.cancellation_attempts.get_by_idempotency_key(
                request.idempotency_key,
            )
            if existing_after_race is None:
                raise AppointmentCancellationInProgressError()

            completed = self._completed_result_from_existing_attempt(
                existing_after_race,
                duplicate=True,
            )
            if completed is not None:
                return completed

            raise AppointmentCancellationInProgressError()

        return self._mutate_cancelled(request, appointment, duplicate=False)

    def _mutate_cancelled(
        self,
        request: AppointmentCancellationRequest,
        appointment: Appointment,
        *,
        duplicate: bool,
    ) -> AppointmentCancellationResult:
        if appointment.status == AppointmentStatus.CANCELLED:
            return AppointmentCancellationResult(
                appointment_id=appointment.id,
                patient_id=appointment.patient_id,
                duplicate=duplicate,
                already_cancelled=True,
                cancelled_at=appointment.cancelled_at,
            )

        appointment.status = AppointmentStatus.CANCELLED
        appointment.cancelled_at = datetime.now(UTC)
        appointment.cancellation_reason = self._normalize_reason(
            request.cancellation_reason,
        )
        self._release_linked_availability_slot(appointment)

        self.audit_logs.record_best_effort(
            AuditLogCreate(
                event_type=AuditEventType.APPOINTMENT_CANCELLATION_CONFIRMED,
                outcome=AuditEventOutcome.SUCCESS,
                actor_type=request.actor_type,
                actor_id=request.actor_id,
                source=request.source or APPOINTMENT_CANCELLATION_SOURCE,
                call_id=request.call_id,
                conversation_id=request.conversation_id,
                patient_id=appointment.patient_id,
                appointment_id=appointment.id,
                event_metadata={
                    "idempotency_key": request.idempotency_key,
                    "duplicate": duplicate,
                    "already_cancelled": False,
                },
            ),
        )

        return AppointmentCancellationResult(
            appointment_id=appointment.id,
            patient_id=appointment.patient_id,
            cancelled_at=appointment.cancelled_at,
            duplicate=duplicate,
        )

    def _claim_execution(
        self,
        request: AppointmentCancellationRequest,
        appointment_id: UUID,
    ) -> AppointmentCancellationAttempt | None:
        try:
            return self.cancellation_attempts.add(
                AppointmentCancellationAttempt(
                    idempotency_key=request.idempotency_key,
                    appointment_id=appointment_id,
                ),
            )
        except IntegrityError:
            return None

    def _completed_result_from_existing_attempt(
        self,
        attempt: AppointmentCancellationAttempt,
        *,
        duplicate: bool,
    ) -> AppointmentCancellationResult | None:
        appointment = self.appointments.get_by_id(attempt.appointment_id)
        if appointment is None:
            msg = "appointment was not found"
            raise AppointmentNotFoundError(msg)

        if appointment.status != AppointmentStatus.CANCELLED:
            return None

        return AppointmentCancellationResult(
            appointment_id=appointment.id,
            patient_id=appointment.patient_id,
            duplicate=duplicate,
            already_cancelled=True,
            cancelled_at=appointment.cancelled_at,
        )

    def _record_attempt_best_effort(
        self,
        request: AppointmentCancellationRequest,
        appointment_id: UUID,
    ) -> None:
        try:
            self.cancellation_attempts.add(
                AppointmentCancellationAttempt(
                    idempotency_key=request.idempotency_key,
                    appointment_id=appointment_id,
                ),
            )
        except IntegrityError:
            existing_attempt = self.cancellation_attempts.get_by_idempotency_key(
                request.idempotency_key,
            )
            if existing_attempt is None:
                return

    def _release_linked_availability_slot(self, appointment: Appointment) -> None:
        if self.availability_slots is None or appointment.availability_slot_id is None:
            return

        slot = self.availability_slots.get_by_id(appointment.availability_slot_id)
        if slot is None:
            return

        slot.status = AvailabilitySlotStatus.AVAILABLE

    def _normalize_reason(self, reason: str | None) -> str | None:
        if reason is None:
            return None

        normalized = reason.strip()
        if not normalized:
            return None

        return normalized
