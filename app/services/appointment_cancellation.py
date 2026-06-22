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
from app.domain.scheduling.enums import AppointmentStatus
from app.models.appointment_cancellation_attempt import AppointmentCancellationAttempt
from app.repositories.appointments import (
    AppointmentCancellationAttemptRepository,
    AppointmentRepository,
)
from app.services.audit_logs import AuditLogCreate, AuditLogService


class AppointmentCancellationService:
    def __init__(
        self,
        *,
        appointments: AppointmentRepository,
        cancellation_attempts: AppointmentCancellationAttemptRepository,
        audit_logs: AuditLogService,
    ) -> None:
        self.appointments = appointments
        self.cancellation_attempts = cancellation_attempts
        self.audit_logs = audit_logs

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
            return self._result_from_existing_attempt(existing_attempt, duplicate=True)

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
            )

        if not is_appointment_cancelable(appointment.status):
            msg = "appointment cannot be cancelled"
            raise AppointmentNotCancelableError(msg)

        appointment.status = AppointmentStatus.CANCELLED
        appointment.cancelled_at = datetime.now(UTC)
        appointment.cancellation_reason = self._normalize_reason(
            request.cancellation_reason,
        )

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
                    "duplicate": False,
                    "already_cancelled": False,
                },
            ),
        )

        self._record_attempt_best_effort(request, appointment.id)

        return AppointmentCancellationResult(
            appointment_id=appointment.id,
            patient_id=appointment.patient_id,
        )

    def _result_from_existing_attempt(
        self,
        attempt: AppointmentCancellationAttempt,
        *,
        duplicate: bool,
    ) -> AppointmentCancellationResult:
        appointment = self.appointments.get_by_id(attempt.appointment_id)
        if appointment is None:
            msg = "appointment was not found"
            raise AppointmentNotFoundError(msg)

        return AppointmentCancellationResult(
            appointment_id=appointment.id,
            patient_id=appointment.patient_id,
            duplicate=duplicate,
            already_cancelled=appointment.status == AppointmentStatus.CANCELLED,
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

    def _normalize_reason(self, reason: str | None) -> str | None:
        if reason is None:
            return None

        normalized = reason.strip()
        if not normalized:
            return None

        return normalized
