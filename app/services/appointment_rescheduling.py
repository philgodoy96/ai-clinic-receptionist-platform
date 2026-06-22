from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

from sqlalchemy.exc import IntegrityError

from app.domain.appointment_rescheduling import (
    APPOINTMENT_RESCHEDULING_SOURCE,
    AppointmentReschedulingHoldExpiredError,
    AppointmentReschedulingNotFoundError,
    AppointmentReschedulingNotReschedulableError,
    AppointmentReschedulingRequest,
    AppointmentReschedulingResult,
    AppointmentReschedulingSlotAlreadyBookedError,
    AppointmentReschedulingSlotNotFoundError,
    AppointmentReschedulingSlotUnavailableError,
    build_reschedule_success_context_updates,
    is_appointment_reschedulable,
    normalize_rescheduling_reason,
    validate_appointment_rescheduling_request,
)
from app.domain.audit.enums import AuditEventOutcome, AuditEventType
from app.domain.scheduling.enums import AppointmentStatus, AvailabilitySlotStatus
from app.models.appointment_reschedule_attempt import AppointmentRescheduleAttempt
from app.models.scheduling import Appointment, AvailabilitySlot
from app.repositories.appointments import AppointmentRescheduleAttemptRepository
from app.repositories.scheduling import (
    AppointmentRepository,
    AvailabilitySlotRepository,
    DoctorRepository,
)
from app.services.appointment_holds import (
    AppointmentHoldMismatchError,
    AppointmentHoldNotFoundError,
    AppointmentHoldOwnershipError,
    AppointmentHoldService,
)
from app.services.audit_logs import AuditLogCreate, AuditLogService
from app.services.conversations import ConversationService
from app.services.email_jobs import (
    AppointmentConfirmationEmailJobCreate,
    EmailJobService,
)


@dataclass(frozen=True, slots=True)
class _ResolvedRescheduleTarget:
    new_slot_id: UUID
    hold_id: UUID | None
    owner_id: str | None


class AppointmentReschedulingService:
    def __init__(
        self,
        *,
        appointments: AppointmentRepository,
        availability_slots: AvailabilitySlotRepository,
        doctors: DoctorRepository,
        hold_service: AppointmentHoldService,
        reschedule_attempts: AppointmentRescheduleAttemptRepository,
        audit_logs: AuditLogService,
        conversations: ConversationService | None = None,
        email_jobs: EmailJobService | None = None,
    ) -> None:
        self.appointments = appointments
        self.availability_slots = availability_slots
        self.doctors = doctors
        self.hold_service = hold_service
        self.reschedule_attempts = reschedule_attempts
        self.audit_logs = audit_logs
        self.conversations = conversations
        self.email_jobs = email_jobs

    def reschedule_appointment(
        self,
        request: AppointmentReschedulingRequest,
    ) -> AppointmentReschedulingResult:
        validate_appointment_rescheduling_request(request)

        existing_attempt = self.reschedule_attempts.get_by_idempotency_key(
            request.idempotency_key,
        )
        if existing_attempt is not None and existing_attempt.new_appointment_id is not None:
            return self._result_from_existing_attempt(existing_attempt, duplicate=True)

        original = self.appointments.get_by_id(request.appointment_id)
        if original is None:
            raise AppointmentReschedulingNotFoundError()

        if original.status == AppointmentStatus.RESCHEDULED:
            successor = self.appointments.find_by_rescheduled_from(
                appointment_id=original.id,
            )
            if successor is not None:
                self._record_attempt_best_effort(
                    request,
                    original_appointment_id=original.id,
                    new_appointment_id=successor.id,
                )
                return AppointmentReschedulingResult(
                    original_appointment_id=original.id,
                    new_appointment_id=successor.id,
                    patient_id=original.patient_id,
                    already_rescheduled=True,
                )

        if not is_appointment_reschedulable(original.status):
            raise AppointmentReschedulingNotReschedulableError()

        target = self._resolve_target(request)
        slot = self._load_and_validate_slot(target.new_slot_id)
        self._validate_hold_if_needed(target=target, slot=slot)
        self._validate_scheduled_conflict(slot=slot)

        original.status = AppointmentStatus.RESCHEDULED
        self._release_original_slot(original)

        new_appointment = Appointment(
            patient_id=original.patient_id,
            doctor_id=slot.doctor_id,
            specialty_id=self._resolve_specialty_id(slot),
            availability_slot_id=slot.id,
            rescheduled_from_appointment_id=original.id,
            start_time=slot.start_time,
            end_time=slot.end_time,
            status=AppointmentStatus.SCHEDULED,
            reason=normalize_rescheduling_reason(request.rescheduling_reason),
        )
        slot.status = AvailabilitySlotStatus.BOOKED
        new_appointment = self.appointments.add(new_appointment)

        confirmation_email_created = self._enqueue_confirmation_email_best_effort(
            request=request,
            new_appointment=new_appointment,
        )

        self.audit_logs.record_best_effort(
            AuditLogCreate(
                event_type=AuditEventType.APPOINTMENT_RESCHEDULING_CONFIRMED,
                outcome=AuditEventOutcome.SUCCESS,
                actor_type=request.actor_type,
                actor_id=request.actor_id,
                source=request.source or APPOINTMENT_RESCHEDULING_SOURCE,
                call_id=request.call_id,
                conversation_id=request.conversation_id,
                patient_id=original.patient_id,
                appointment_id=new_appointment.id,
                availability_slot_id=slot.id,
                event_metadata={
                    "idempotency_key": request.idempotency_key,
                    "duplicate": False,
                    "already_rescheduled": False,
                    "original_appointment_id": str(original.id),
                    "new_appointment_id": str(new_appointment.id),
                },
            ),
        )

        self._record_attempt_best_effort(
            request,
            original_appointment_id=original.id,
            new_appointment_id=new_appointment.id,
        )
        self._update_conversation_metadata_best_effort(
            request=request,
            new_appointment=new_appointment,
            slot=slot,
            hold_used=target.hold_id is not None,
        )
        self._release_hold_best_effort(target=target, slot=slot)

        return AppointmentReschedulingResult(
            original_appointment_id=original.id,
            new_appointment_id=new_appointment.id,
            patient_id=original.patient_id,
            confirmation_email_created=confirmation_email_created,
        )

    def _result_from_existing_attempt(
        self,
        attempt: AppointmentRescheduleAttempt,
        *,
        duplicate: bool,
    ) -> AppointmentReschedulingResult:
        original = self.appointments.get_by_id(attempt.appointment_id)
        if original is None:
            raise AppointmentReschedulingNotFoundError()

        new_appointment_id = attempt.new_appointment_id
        if new_appointment_id is None:
            successor = self.appointments.find_by_rescheduled_from(
                appointment_id=original.id,
            )
            if successor is None:
                raise AppointmentReschedulingNotFoundError(
                    "rescheduled appointment was not found",
                )
            new_appointment_id = successor.id

        return AppointmentReschedulingResult(
            original_appointment_id=original.id,
            new_appointment_id=new_appointment_id,
            patient_id=original.patient_id,
            duplicate=duplicate,
            already_rescheduled=original.status == AppointmentStatus.RESCHEDULED,
        )

    def _resolve_target(
        self,
        request: AppointmentReschedulingRequest,
    ) -> _ResolvedRescheduleTarget:
        owner_id = (request.owner_id or "").strip() or None
        hold_id = request.hold_id
        new_slot_id = request.new_slot_id

        if hold_id is not None:
            hold = self.hold_service.get_hold_by_id(hold_id)
            if hold is None:
                raise AppointmentReschedulingHoldExpiredError()

            if new_slot_id is None:
                new_slot_id = hold.availability_slot_id
            elif new_slot_id != hold.availability_slot_id:
                raise AppointmentReschedulingHoldExpiredError(
                    "appointment hold does not match the requested slot",
                )

        if new_slot_id is None:
            msg = "hold_id or new_slot_id is required"
            raise AppointmentReschedulingSlotNotFoundError(msg)

        return _ResolvedRescheduleTarget(
            new_slot_id=new_slot_id,
            hold_id=hold_id,
            owner_id=owner_id,
        )

    def _load_and_validate_slot(self, slot_id: UUID) -> AvailabilitySlot:
        slot = self.availability_slots.get_by_id(slot_id)
        if slot is None:
            raise AppointmentReschedulingSlotNotFoundError()

        if slot.status != AvailabilitySlotStatus.AVAILABLE:
            raise AppointmentReschedulingSlotUnavailableError()

        doctor = self.doctors.get_by_id(slot.doctor_id)
        if doctor is None or not doctor.is_active:
            raise AppointmentReschedulingSlotUnavailableError(
                "doctor was not found or is inactive",
            )

        return slot

    def _validate_hold_if_needed(
        self,
        *,
        target: _ResolvedRescheduleTarget,
        slot: AvailabilitySlot,
    ) -> None:
        if target.hold_id is None:
            return

        owner_id = target.owner_id
        if owner_id is None:
            raise AppointmentReschedulingHoldExpiredError("owner_id is required")

        try:
            self.hold_service.validate_hold(
                hold_id=target.hold_id,
                doctor_id=slot.doctor_id,
                start_time=slot.start_time,
                owner_id=owner_id,
            )
        except (
            AppointmentHoldNotFoundError,
            AppointmentHoldMismatchError,
            AppointmentHoldOwnershipError,
        ) as exc:
            raise AppointmentReschedulingHoldExpiredError(str(exc)) from exc

    def _validate_scheduled_conflict(self, *, slot: AvailabilitySlot) -> None:
        existing_appointment = self.appointments.find_scheduled_conflict(
            doctor_id=slot.doctor_id,
            start_time=slot.start_time,
        )
        if existing_appointment is not None:
            raise AppointmentReschedulingSlotAlreadyBookedError()

    def _resolve_specialty_id(self, slot: AvailabilitySlot) -> UUID:
        doctor = self.doctors.get_by_id(slot.doctor_id)
        if doctor is None:
            raise AppointmentReschedulingSlotUnavailableError(
                "doctor was not found or is inactive",
            )
        return doctor.specialty_id

    def _release_original_slot(self, original: Appointment) -> None:
        if original.availability_slot_id is None:
            return

        original_slot = self.availability_slots.get_by_id(original.availability_slot_id)
        if original_slot is None:
            return

        original_slot.status = AvailabilitySlotStatus.AVAILABLE

    def _enqueue_confirmation_email_best_effort(
        self,
        *,
        request: AppointmentReschedulingRequest,
        new_appointment: Appointment,
    ) -> bool:
        if self.email_jobs is None:
            return False

        email_job_result = self.email_jobs.get_or_create_appointment_confirmation_email_job(
            AppointmentConfirmationEmailJobCreate(
                appointment_id=new_appointment.id,
                patient_id=new_appointment.patient_id,
                appointment_start_time=new_appointment.start_time.isoformat(),
                payload={
                    "source": request.source or APPOINTMENT_RESCHEDULING_SOURCE,
                    "original_appointment_id": str(request.appointment_id),
                    "conversation_id": request.conversation_id,
                },
            ),
        )
        return email_job_result.created

    def _update_conversation_metadata_best_effort(
        self,
        *,
        request: AppointmentReschedulingRequest,
        new_appointment: Appointment,
        slot: AvailabilitySlot,
        hold_used: bool,
    ) -> None:
        if self.conversations is None or not request.conversation_id:
            return

        try:
            conversation_id = UUID(request.conversation_id.strip())
        except ValueError:
            return

        if hold_used:
            self.conversations.clear_voice_active_hold(conversation_id=conversation_id)

        self.conversations.merge_voice_context(
            conversation_id=conversation_id,
            voice_context=build_reschedule_success_context_updates(
                appointment_id=new_appointment.id,
                availability_slot_id=slot.id,
                start_time=new_appointment.start_time.isoformat(),
                end_time=new_appointment.end_time.isoformat(),
            ),
        )

    def _release_hold_best_effort(
        self,
        *,
        target: _ResolvedRescheduleTarget,
        slot: AvailabilitySlot,
    ) -> None:
        if target.hold_id is None or target.owner_id is None:
            return

        self.hold_service.release_hold(
            doctor_id=slot.doctor_id,
            start_time=slot.start_time,
            owner_id=target.owner_id,
        )

    def _record_attempt_best_effort(
        self,
        request: AppointmentReschedulingRequest,
        *,
        original_appointment_id: UUID,
        new_appointment_id: UUID,
    ) -> None:
        try:
            self.reschedule_attempts.add(
                AppointmentRescheduleAttempt(
                    idempotency_key=request.idempotency_key,
                    appointment_id=original_appointment_id,
                    new_appointment_id=new_appointment_id,
                ),
            )
        except IntegrityError:
            existing_attempt = self.reschedule_attempts.get_by_idempotency_key(
                request.idempotency_key,
            )
            if existing_attempt is None:
                return
