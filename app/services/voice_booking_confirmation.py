from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Protocol
from uuid import UUID

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.domain.audit.enums import AuditActorType, AuditEventOutcome, AuditEventType
from app.domain.scheduling.appointment_holds import AppointmentHold
from app.domain.voice_booking import (
    VOICE_BOOKING_SOURCE,
    VoiceBookingConfirmationError,
    VoiceBookingConfirmationRequest,
    VoiceBookingConfirmationResult,
    VoiceBookingExpiredHoldError,
    VoiceBookingHoldOwnershipError,
    VoiceBookingIdentityConfirmationRequiredError,
    VoiceBookingIdentityNotResolvedError,
    VoiceBookingMissingConfirmationError,
    VoiceBookingMissingContextError,
    VoiceBookingMissingHoldError,
    VoiceBookingMissingIdentityError,
    VoiceBookingPatientIdentity,
    VoiceBookingPatientNotFoundError,
    VoiceBookingQuotaExceededError,
    VoiceBookingResolutionIdRequiredError,
    VoiceBookingTemporaryFailureError,
    is_voice_booking_patient_identity_complete,
    resolve_voice_booking_owner_id,
    validate_voice_context_hold_reference,
)
from app.domain.voice_booking_enums import VoiceBookingAttemptStatus
from app.domain.voice_conversation import read_voice_context
from app.domain.voice_patient_intake import (
    PatientIntakeIdentity,
    PatientIntakeNotFoundError,
    VoicePatientIntakeMode,
)
from app.messaging.email_job_dispatch import EmailJobDispatchPublisherError
from app.models.voice_booking_attempt import VoiceBookingAttempt
from app.repositories.scheduling import AppointmentRepository, AvailabilitySlotRepository
from app.repositories.voice_booking_attempts import VoiceBookingAttemptRepository
from app.services.appointment_booking import (
    AppointmentBookingRequest,
    AppointmentBookingService,
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
from app.services.audit_logs import AuditLogCreate, AuditLogService
from app.services.conversations import ConversationService
from app.services.demo_guardrails import (
    APPOINTMENTS_PER_DAY_PER_IP,
    GLOBAL_APPOINTMENTS_PER_DAY,
    DemoGuardrailLimitExceeded,
    DemoGuardrailService,
)
from app.services.email_jobs import (
    AppointmentConfirmationEmailJobCreate,
    EmailJobService,
)
from app.services.patient_identity_resolution import (
    PatientIdentityConfirmationRequiredError,
    PatientIdentityResolutionService,
    PatientResolutionNotFoundError,
)
from app.services.patient_intake import PatientIntakeService
from app.services.scheduling import (
    InsufficientPatientIdentityError,
    SchedulingService,
)

logger = logging.getLogger("app.voice_booking_confirmation")


class EmailJobDispatchPublisherProtocol(Protocol):
    def publish_email_job_ready(self, *, email_job_id: UUID) -> None:
        raise NotImplementedError


@dataclass(frozen=True, slots=True)
class _ResolvedVoiceBookingTargets:
    hold_id: UUID
    availability_slot_id: UUID
    owner_id: str


class VoiceBookingConfirmationService:
    def __init__(
        self,
        *,
        db: Session,
        booking_service: AppointmentBookingService,
        hold_service: AppointmentHoldService,
        scheduling_service: SchedulingService,
        conversations: ConversationService,
        audit_logs: AuditLogService,
        email_jobs: EmailJobService,
        voice_booking_attempts: VoiceBookingAttemptRepository,
        appointments: AppointmentRepository,
        availability_slots: AvailabilitySlotRepository,
        email_job_dispatch: EmailJobDispatchPublisherProtocol | None = None,
        demo_guardrails: DemoGuardrailService | None = None,
        patient_intake: PatientIntakeService | None = None,
        patient_identity_resolution: PatientIdentityResolutionService | None = None,
    ) -> None:
        self.db = db
        self.booking_service = booking_service
        self.hold_service = hold_service
        self.scheduling_service = scheduling_service
        self.conversations = conversations
        self.audit_logs = audit_logs
        self.email_jobs = email_jobs
        self.voice_booking_attempts = voice_booking_attempts
        self.appointments = appointments
        self.availability_slots = availability_slots
        self.email_job_dispatch = email_job_dispatch
        self.demo_guardrails = demo_guardrails
        self.patient_intake = patient_intake or PatientIntakeService(
            patients=scheduling_service.patients,
            mode=VoicePatientIntakeMode.LOOKUP_ONLY,
            db=db,
        )
        self.patient_identity_resolution = patient_identity_resolution

    def confirm_and_book(
        self,
        request: VoiceBookingConfirmationRequest,
    ) -> VoiceBookingConfirmationResult:
        self._validate_request(request)

        conversation = self.conversations.get_conversation(request.conversation_id)
        voice_context = read_voice_context(conversation.conversation_metadata)

        existing_attempt = self.voice_booking_attempts.get_by_idempotency_key(
            request.idempotency_key,
        )
        if existing_attempt is not None and existing_attempt.appointment_id is not None:
            return self._result_from_existing_attempt(
                existing_attempt,
                duplicate=True,
            )

        targets = self._resolve_targets(request, voice_context)
        validate_voice_context_hold_reference(
            conversation_metadata=conversation.conversation_metadata,
            hold_id=str(targets.hold_id),
        )

        hold = self._load_and_validate_hold(
            hold_id=targets.hold_id,
            availability_slot_id=targets.availability_slot_id,
            owner_id=targets.owner_id,
        )

        patient = self._resolve_patient(request, voice_context)
        self._check_demo_quotas(request.client_ip)

        attempt = existing_attempt or self._create_pending_attempt(
            request=request,
            targets=targets,
            patient_id=patient.id,
        )

        try:
            booking_result = self.booking_service.book_appointment(
                AppointmentBookingRequest(
                    hold_id=targets.hold_id,
                    availability_slot_id=targets.availability_slot_id,
                    patient_id=patient.id,
                    owner_id=targets.owner_id,
                    reason=request.notes,
                ),
            )
        except IntegrityError as exc:
            self.db.rollback()
            self._mark_attempt_failed(attempt, error_code="appointment_conflict")
            self._record_booking_failure(
                request=request,
                owner_id=targets.owner_id,
                patient_id=patient.id,
                availability_slot_id=targets.availability_slot_id,
                hold_id=str(targets.hold_id),
                error_code="appointment_conflict",
            )
            raise VoiceBookingTemporaryFailureError(
                error_code="appointment_conflict",
                message="The selected slot is no longer available.",
            ) from exc
        except (
            AppointmentHoldNotFoundError,
            AppointmentHoldMismatchError,
            AppointmentHoldOwnershipError,
        ) as exc:
            self.db.rollback()
            self._mark_attempt_failed(attempt, error_code="appointment_hold_expired")
            self._record_booking_failure(
                request=request,
                owner_id=targets.owner_id,
                patient_id=patient.id,
                availability_slot_id=targets.availability_slot_id,
                hold_id=str(targets.hold_id),
                error_code="appointment_hold_expired",
            )
            raise VoiceBookingExpiredHoldError(str(exc)) from exc
        except (
            AppointmentSlotAlreadyBookedError,
            BookingAvailabilitySlotUnavailableError,
            BookingAvailabilitySlotNotFoundError,
            BookingDoctorNotFoundError,
            BookingPatientNotFoundError,
        ) as exc:
            self.db.rollback()
            error_code = self._map_booking_error_code(exc)
            self._mark_attempt_failed(attempt, error_code=error_code)
            self._record_booking_failure(
                request=request,
                owner_id=targets.owner_id,
                patient_id=patient.id,
                availability_slot_id=targets.availability_slot_id,
                hold_id=str(targets.hold_id),
                error_code=error_code,
            )
            raise VoiceBookingTemporaryFailureError(
                error_code=error_code,
                message=str(exc),
            ) from exc

        appointment = booking_result.appointment
        email_job_result = self.email_jobs.get_or_create_appointment_confirmation_email_job(
            AppointmentConfirmationEmailJobCreate(
                appointment_id=appointment.id,
                patient_id=patient.id,
                appointment_start_time=appointment.start_time.isoformat(),
                payload={
                    "source": VOICE_BOOKING_SOURCE,
                    "hold_id": str(targets.hold_id),
                    "provider_call_id": request.provider_call_id,
                    "conversation_id": str(request.conversation_id),
                    "voice_call_id": str(request.voice_call_id),
                },
            ),
        )

        self.audit_logs.record_best_effort(
            AuditLogCreate(
                event_type=AuditEventType.APPOINTMENT_BOOKING_CONFIRMED,
                outcome=AuditEventOutcome.SUCCESS,
                actor_type=AuditActorType.RETELL,
                source=VOICE_BOOKING_SOURCE,
                actor_id=targets.owner_id,
                call_id=request.provider_call_id,
                conversation_id=str(request.conversation_id),
                patient_id=patient.id,
                appointment_id=appointment.id,
                availability_slot_id=targets.availability_slot_id,
                event_metadata={
                    "hold_id": str(targets.hold_id),
                    "idempotency_key": request.idempotency_key,
                    "duplicate": False,
                },
            ),
        )

        attempt.status = VoiceBookingAttemptStatus.SUCCEEDED
        attempt.appointment_id = appointment.id
        attempt.patient_id = patient.id
        attempt.availability_slot_id = targets.availability_slot_id
        attempt.hold_id = str(targets.hold_id)
        attempt.error_code = None
        attempt.updated_at = datetime.now(UTC)
        self.voice_booking_attempts.update(attempt)

        self.conversations.clear_voice_active_hold(
            conversation_id=request.conversation_id,
        )
        self.conversations.merge_voice_context(
            conversation_id=request.conversation_id,
            voice_context={"appointment_id": str(appointment.id)},
        )

        self.db.commit()
        self.db.refresh(appointment)

        if email_job_result.created and self.demo_guardrails is not None and request.client_ip:
            self.demo_guardrails.record_confirmation_email_created(request.client_ip)

        if request.client_ip and self.demo_guardrails is not None:
            self.demo_guardrails.record_appointment_created(request.client_ip)

        self._publish_email_job_best_effort(email_job_result.email_job.id)

        self.hold_service.release_hold(
            doctor_id=hold.doctor_id,
            start_time=hold.start_time,
            owner_id=targets.owner_id,
        )

        return VoiceBookingConfirmationResult(
            appointment_id=appointment.id,
            patient_id=patient.id,
            availability_slot_id=targets.availability_slot_id,
            hold_id=str(targets.hold_id),
            duplicate=False,
            confirmation_email_created=email_job_result.created,
        )

    def _validate_request(self, request: VoiceBookingConfirmationRequest) -> None:
        if not request.provider_call_id.strip():
            msg = "provider_call_id is required"
            raise VoiceBookingMissingContextError(msg)

        if request.conversation_id is None:
            msg = "conversation_id is required"
            raise VoiceBookingMissingContextError(msg)

        if not request.explicit_confirmation:
            msg = "explicit_confirmation is required"
            raise VoiceBookingMissingConfirmationError(msg)

        if _has_patient_resolution_id(request):
            return

        identity = VoiceBookingPatientIdentity(
            patient_name=request.patient_name,
            patient_date_of_birth=request.patient_date_of_birth,
            patient_email=request.patient_email,
            patient_phone=request.patient_phone,
        )
        if not is_voice_booking_patient_identity_complete(identity):
            msg = "patient identity is incomplete"
            raise VoiceBookingMissingIdentityError(msg)

    def _resolve_targets(
        self,
        request: VoiceBookingConfirmationRequest,
        voice_context: dict[str, Any],
    ) -> _ResolvedVoiceBookingTargets:
        owner_id = resolve_voice_booking_owner_id(
            provider_call_id=request.provider_call_id,
            conversation_id=request.conversation_id,
        )

        hold_id = self._resolve_hold_id(request, voice_context)
        availability_slot_id = self._resolve_slot_id(request, voice_context, hold_id)

        return _ResolvedVoiceBookingTargets(
            hold_id=hold_id,
            availability_slot_id=availability_slot_id,
            owner_id=owner_id,
        )

    def _resolve_hold_id(
        self,
        request: VoiceBookingConfirmationRequest,
        voice_context: dict[str, Any],
    ) -> UUID:
        if request.hold_id and request.hold_id.strip():
            return UUID(request.hold_id.strip())

        context_hold_id = voice_context.get("hold_id")
        if context_hold_id:
            return UUID(str(context_hold_id))

        msg = "hold_id is required"
        raise VoiceBookingMissingHoldError(msg)

    def _resolve_slot_id(
        self,
        request: VoiceBookingConfirmationRequest,
        voice_context: dict[str, Any],
        hold_id: UUID,
    ) -> UUID:
        if request.slot_id is not None and str(request.slot_id).strip():
            return UUID(str(request.slot_id))

        context_slot_id = voice_context.get("availability_slot_id")
        if context_slot_id:
            return UUID(str(context_slot_id))

        hold = self.hold_service.get_hold_by_id(hold_id)
        if hold is not None:
            return hold.availability_slot_id

        msg = "slot_id is required"
        raise VoiceBookingMissingHoldError(msg)

    def _load_and_validate_hold(
        self,
        *,
        hold_id: UUID,
        availability_slot_id: UUID,
        owner_id: str,
    ) -> AppointmentHold:
        hold = self.hold_service.get_hold_by_id(hold_id)
        if hold is None:
            msg = "appointment hold was not found or expired"
            raise VoiceBookingExpiredHoldError(msg)

        slot = self.availability_slots.get_by_id(availability_slot_id)
        if slot is None:
            msg = "availability slot was not found"
            raise VoiceBookingExpiredHoldError(msg)

        try:
            return self.hold_service.validate_hold(
                hold_id=hold_id,
                doctor_id=slot.doctor_id,
                start_time=slot.start_time,
                owner_id=owner_id,
            )
        except AppointmentHoldOwnershipError as exc:
            msg = "appointment hold does not belong to this voice call"
            raise VoiceBookingHoldOwnershipError(msg) from exc
        except (AppointmentHoldNotFoundError, AppointmentHoldMismatchError) as exc:
            msg = "appointment hold was not found or expired"
            raise VoiceBookingExpiredHoldError(msg) from exc

    def _resolve_patient(
        self,
        request: VoiceBookingConfirmationRequest,
        voice_context: dict[str, Any] | None = None,
    ) -> Any:
        effective_resolution_id = _effective_patient_resolution_id(request, voice_context)
        identity_was_resolved_on_call = _voice_context_has_patient_resolution_id(voice_context)

        if effective_resolution_id is not None:
            try:
                return self._resolve_patient_from_resolution_token(
                    request,
                    patient_resolution_id=effective_resolution_id,
                )
            except PatientResolutionNotFoundError as exc:
                if identity_was_resolved_on_call and not _has_patient_resolution_id(request):
                    msg = (
                        "patient_resolution_id is required for booking after "
                        "identity resolution"
                    )
                    raise VoiceBookingResolutionIdRequiredError(msg) from exc
                msg = "patient identity is not resolved"
                raise VoiceBookingIdentityNotResolvedError(msg) from exc

        try:
            return self.patient_intake.resolve_for_voice_booking(
                PatientIntakeIdentity(
                    full_name=request.patient_name,
                    date_of_birth=request.patient_date_of_birth,
                    email=request.patient_email,
                    phone_number=request.patient_phone,
                ),
            )
        except InsufficientPatientIdentityError as exc:
            msg = "patient identity is incomplete"
            raise VoiceBookingMissingIdentityError(msg) from exc
        except PatientIntakeNotFoundError as exc:
            if identity_was_resolved_on_call and not _has_patient_resolution_id(request):
                msg = (
                    "patient_resolution_id is required for booking after "
                    "identity resolution"
                )
                raise VoiceBookingResolutionIdRequiredError(msg) from exc
            msg = "patient was not found"
            raise VoiceBookingPatientNotFoundError(msg) from exc

    def _resolve_patient_from_resolution_token(
        self,
        request: VoiceBookingConfirmationRequest,
        *,
        patient_resolution_id: str | None = None,
    ) -> Any:
        if self.patient_identity_resolution is None:
            msg = "patient identity resolution is unavailable"
            raise VoiceBookingIdentityNotResolvedError(msg)

        resolution_id = patient_resolution_id or request.patient_resolution_id
        if resolution_id is None or resolution_id.strip() == "":
            msg = "patient_resolution_id is required"
            raise VoiceBookingIdentityNotResolvedError(msg)

        try:
            return self.patient_identity_resolution.resolve_patient_for_booking(
                patient_resolution_id=resolution_id,
                provider_call_id=request.provider_call_id,
                conversation_id=request.conversation_id,
            )
        except PatientIdentityConfirmationRequiredError as exc:
            msg = "patient identity confirmation is required"
            raise VoiceBookingIdentityConfirmationRequiredError(msg) from exc

    def _check_demo_quotas(self, client_ip: str | None) -> None:
        if self.demo_guardrails is None or not client_ip:
            return

        try:
            self.demo_guardrails.check_appointment_creation_allowed(client_ip)
        except DemoGuardrailLimitExceeded as exc:
            if exc.limit_name in {
                APPOINTMENTS_PER_DAY_PER_IP,
                GLOBAL_APPOINTMENTS_PER_DAY,
            }:
                raise VoiceBookingQuotaExceededError(exc.limit_name) from exc
            raise

    def _create_pending_attempt(
        self,
        *,
        request: VoiceBookingConfirmationRequest,
        targets: _ResolvedVoiceBookingTargets,
        patient_id: UUID,
    ) -> VoiceBookingAttempt:
        attempt = VoiceBookingAttempt(
            idempotency_key=request.idempotency_key,
            provider=request.provider,
            provider_call_id=request.provider_call_id,
            tool_call_id=request.tool_call_id,
            voice_call_id=request.voice_call_id,
            conversation_id=request.conversation_id,
            hold_id=str(targets.hold_id),
            availability_slot_id=targets.availability_slot_id,
            patient_id=patient_id,
            status=VoiceBookingAttemptStatus.PENDING,
            attempt_metadata={
                "provider_call_id": request.provider_call_id,
                "conversation_id": str(request.conversation_id),
            },
        )

        try:
            return self.voice_booking_attempts.add(attempt)
        except IntegrityError:
            self.db.rollback()
            existing = self.voice_booking_attempts.get_by_idempotency_key(
                request.idempotency_key,
            )
            if existing is None:
                raise
            if existing.appointment_id is not None:
                return existing
            return existing

    def _result_from_existing_attempt(
        self,
        attempt: VoiceBookingAttempt,
        *,
        duplicate: bool,
    ) -> VoiceBookingConfirmationResult:
        if attempt.appointment_id is None:
            msg = "voice booking attempt is incomplete"
            raise VoiceBookingConfirmationError(msg)

        if attempt.patient_id is None or attempt.availability_slot_id is None:
            appointment = self.appointments.get_by_id(attempt.appointment_id)
            if appointment is None:
                msg = "appointment was not found for duplicate voice booking attempt"
                raise VoiceBookingConfirmationError(msg)

            availability_slot_id = appointment.availability_slot_id or attempt.availability_slot_id
            if availability_slot_id is None:
                msg = "availability slot was not found for duplicate voice booking attempt"
                raise VoiceBookingConfirmationError(msg)

            return VoiceBookingConfirmationResult(
                appointment_id=appointment.id,
                patient_id=appointment.patient_id,
                availability_slot_id=availability_slot_id,
                hold_id=attempt.hold_id or "",
                duplicate=duplicate,
                confirmation_email_created=False,
            )

        return VoiceBookingConfirmationResult(
            appointment_id=attempt.appointment_id,
            patient_id=attempt.patient_id,
            availability_slot_id=attempt.availability_slot_id,
            hold_id=attempt.hold_id or "",
            duplicate=duplicate,
            confirmation_email_created=False,
        )

    def _mark_attempt_failed(self, attempt: VoiceBookingAttempt, *, error_code: str) -> None:
        attempt.status = VoiceBookingAttemptStatus.FAILED
        attempt.error_code = error_code
        attempt.updated_at = datetime.now(UTC)
        self.voice_booking_attempts.update(attempt)
        self.db.commit()

    def _record_booking_failure(
        self,
        *,
        request: VoiceBookingConfirmationRequest,
        owner_id: str,
        patient_id: UUID | None,
        availability_slot_id: UUID,
        hold_id: str,
        error_code: str,
    ) -> None:
        self._commit_audit_best_effort(
            AuditLogCreate(
                event_type=AuditEventType.APPOINTMENT_BOOKING_FAILED,
                outcome=AuditEventOutcome.FAILURE,
                actor_type=AuditActorType.RETELL,
                source=VOICE_BOOKING_SOURCE,
                actor_id=owner_id,
                call_id=request.provider_call_id,
                conversation_id=str(request.conversation_id),
                patient_id=patient_id,
                availability_slot_id=availability_slot_id,
                event_metadata={
                    "hold_id": hold_id,
                    "reason": error_code,
                    "idempotency_key": request.idempotency_key,
                },
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

    def _publish_email_job_best_effort(self, email_job_id: UUID) -> None:
        if self.email_job_dispatch is None:
            return

        try:
            self.email_job_dispatch.publish_email_job_ready(email_job_id=email_job_id)
        except EmailJobDispatchPublisherError:
            logger.warning(
                "email_job_dispatch_publish_failed",
                extra={
                    "event": "email_job_dispatch_publish_failed",
                    "email_job_id": str(email_job_id),
                },
            )

    def _map_booking_error_code(self, exc: Exception) -> str:
        if isinstance(exc, AppointmentSlotAlreadyBookedError):
            return "slot_already_booked"
        if isinstance(exc, BookingAvailabilitySlotUnavailableError):
            return "availability_slot_unavailable"
        if isinstance(exc, BookingAvailabilitySlotNotFoundError):
            return "availability_slot_not_found"
        if isinstance(exc, BookingDoctorNotFoundError):
            return "doctor_not_found"
        if isinstance(exc, BookingPatientNotFoundError):
            return "patient_not_found"
        return "booking_failed"


def _has_patient_resolution_id(request: VoiceBookingConfirmationRequest) -> bool:
    return (
        request.patient_resolution_id is not None
        and request.patient_resolution_id.strip() != ""
    )


def _voice_context_has_patient_resolution_id(voice_context: dict[str, Any] | None) -> bool:
    if not voice_context:
        return False

    context_resolution_id = voice_context.get("patient_resolution_id")
    return context_resolution_id is not None and str(context_resolution_id).strip() != ""


def _effective_patient_resolution_id(
    request: VoiceBookingConfirmationRequest,
    voice_context: dict[str, Any] | None,
) -> str | None:
    if _has_patient_resolution_id(request):
        assert request.patient_resolution_id is not None
        return request.patient_resolution_id.strip()

    if not voice_context:
        return None

    context_resolution_id = voice_context.get("patient_resolution_id")
    if context_resolution_id is None:
        return None

    normalized = str(context_resolution_id).strip()
    return normalized or None
