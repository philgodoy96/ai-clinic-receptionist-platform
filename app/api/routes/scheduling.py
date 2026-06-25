import logging
from collections.abc import Sequence
from datetime import datetime
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.api.dependencies import (
    get_appointment_booking_service,
    get_appointment_hold_service,
    get_audit_log_service,
    get_email_job_dispatch_publisher,
    get_email_job_service,
    get_scheduling_service,
)
from app.api.errors import APIError
from app.db.session import get_db
from app.domain.audit.enums import AuditActorType, AuditEventOutcome, AuditEventType
from app.messaging.email_job_dispatch import (
    EmailJobDispatchPublisher,
    EmailJobDispatchPublisherError,
)
from app.models.scheduling import Appointment, AvailabilitySlot, Doctor, Patient, Specialty
from app.schemas.scheduling import (
    AppointmentBookingRequestBody,
    AppointmentHoldCreateRequest,
    AppointmentHoldResponse,
    AppointmentResponse,
    AvailabilitySlotResponse,
    DoctorResponse,
    PatientLookupRequest,
    PatientResponse,
    SpecialtyResponse,
    UpcomingAppointmentsRequest,
)
from app.services.appointment_booking import (
    AppointmentBookingOwnerRequiredError,
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
    AppointmentHoldStoreUnavailableError,
    AppointmentSlotAlreadyHeldError,
    InvalidAppointmentHoldOwnerError,
    InvalidAppointmentHoldWindowError,
)
from app.services.audit_logs import AuditLogCreate, AuditLogService
from app.services.email_jobs import (
    AppointmentConfirmationEmailJobCreate,
    EmailJobService,
)
from app.services.scheduling import (
    AvailabilitySlotNotFoundError,
    AvailabilitySlotUnavailableError,
    DoctorNotFoundError,
    InsufficientPatientIdentityError,
    InvalidAvailabilityWindowError,
    PatientLookupCriteria,
    SchedulingService,
)

router = APIRouter(prefix="/api/v1/scheduling", tags=["scheduling"])

SCHEDULING_API_SOURCE = "scheduling_api"
logger = logging.getLogger("app.scheduling")


def _commit_audit_best_effort(
    db: Session,
    audit_logs: AuditLogService,
    payload: AuditLogCreate,
) -> None:
    try:
        audit_logs.record(payload)
        db.commit()
    except Exception:
        try:
            db.rollback()
        except Exception:
            return


@router.get("/specialties", response_model=list[SpecialtyResponse])
def list_specialties(
    service: Annotated[SchedulingService, Depends(get_scheduling_service)],
) -> Sequence[Specialty]:
    return service.list_specialties()


@router.get("/doctors", response_model=list[DoctorResponse])
def list_doctors(
    service: Annotated[SchedulingService, Depends(get_scheduling_service)],
    specialty_id: Annotated[UUID | None, Query()] = None,
) -> Sequence[Doctor]:
    return service.list_doctors(specialty_id=specialty_id)


@router.get(
    "/doctors/{doctor_id}/availability",
    response_model=list[AvailabilitySlotResponse],
)
def check_doctor_availability(
    doctor_id: UUID,
    start_from: datetime,
    start_to: datetime,
    service: Annotated[SchedulingService, Depends(get_scheduling_service)],
) -> Sequence[AvailabilitySlot]:
    try:
        return service.check_availability(
            doctor_id=doctor_id,
            start_from=start_from,
            start_to=start_to,
        )
    except InvalidAvailabilityWindowError as exc:
        raise APIError(
            status_code=status.HTTP_400_BAD_REQUEST,
            code="invalid_availability_window",
            message="start_to must be greater than start_from",
        ) from exc
    except DoctorNotFoundError as exc:
        raise APIError(
            status_code=status.HTTP_404_NOT_FOUND,
            code="doctor_not_found",
            message="doctor was not found or is inactive",
        ) from exc


@router.post("/patients/lookup", response_model=PatientResponse)
def lookup_patient(
    payload: PatientLookupRequest,
    service: Annotated[SchedulingService, Depends(get_scheduling_service)],
) -> Patient:
    criteria = PatientLookupCriteria(
        full_name=payload.full_name,
        date_of_birth=payload.date_of_birth,
        phone_number=payload.phone_number,
        email=payload.email,
    )

    try:
        patient = service.lookup_patient(criteria)
    except InsufficientPatientIdentityError as exc:
        raise APIError(
            status_code=status.HTTP_400_BAD_REQUEST,
            code="invalid_patient_identity",
            message="patient lookup requires phone_number or email",
        ) from exc

    if patient is None:
        raise APIError(
            status_code=status.HTTP_404_NOT_FOUND,
            code="patient_not_found",
            message="patient was not found",
        )

    return patient


@router.post(
    "/patients/upcoming-appointments",
    response_model=list[AppointmentResponse],
)
def list_upcoming_appointments(
    payload: UpcomingAppointmentsRequest,
    service: Annotated[SchedulingService, Depends(get_scheduling_service)],
) -> Sequence[Appointment]:
    criteria = PatientLookupCriteria(
        full_name=payload.full_name,
        date_of_birth=payload.date_of_birth,
        phone_number=payload.phone_number,
        email=payload.email,
    )

    try:
        return service.list_upcoming_appointments(
            criteria=criteria,
            start_from=payload.start_from,
        )
    except InsufficientPatientIdentityError as exc:
        raise APIError(
            status_code=status.HTTP_400_BAD_REQUEST,
            code="invalid_patient_identity",
            message="patient lookup requires phone_number or email",
        ) from exc


@router.post(
    "/appointment-holds",
    response_model=AppointmentHoldResponse,
    status_code=status.HTTP_201_CREATED,
)
def hold_appointment_slot(
    payload: AppointmentHoldCreateRequest,
    db: Annotated[Session, Depends(get_db)],
    scheduling_service: Annotated[SchedulingService, Depends(get_scheduling_service)],
    hold_service: Annotated[AppointmentHoldService, Depends(get_appointment_hold_service)],
    audit_logs: Annotated[AuditLogService, Depends(get_audit_log_service)],
) -> AppointmentHoldResponse:
    try:
        slot = scheduling_service.get_available_slot_for_hold(payload.availability_slot_id)
        hold = hold_service.create_hold(
            availability_slot_id=slot.id,
            doctor_id=slot.doctor_id,
            start_time=slot.start_time,
            end_time=slot.end_time,
            owner_id=payload.owner_id,
        )
    except AvailabilitySlotNotFoundError as exc:
        _commit_audit_best_effort(
            db,
            audit_logs,
            AuditLogCreate(
                event_type=AuditEventType.APPOINTMENT_HOLD_FAILED,
                outcome=AuditEventOutcome.FAILURE,
                actor_type=AuditActorType.API,
                source=SCHEDULING_API_SOURCE,
                actor_id=payload.owner_id,
                availability_slot_id=payload.availability_slot_id,
                event_metadata={"reason": "availability_slot_not_found"},
            ),
        )
        raise APIError(
            status_code=status.HTTP_404_NOT_FOUND,
            code="availability_slot_not_found",
            message="availability slot was not found",
        ) from exc
    except AvailabilitySlotUnavailableError as exc:
        _commit_audit_best_effort(
            db,
            audit_logs,
            AuditLogCreate(
                event_type=AuditEventType.APPOINTMENT_HOLD_FAILED,
                outcome=AuditEventOutcome.FAILURE,
                actor_type=AuditActorType.API,
                source=SCHEDULING_API_SOURCE,
                actor_id=payload.owner_id,
                availability_slot_id=payload.availability_slot_id,
                event_metadata={"reason": "availability_slot_unavailable"},
            ),
        )
        raise APIError(
            status_code=status.HTTP_409_CONFLICT,
            code="availability_slot_unavailable",
            message="availability slot is not available",
        ) from exc
    except AppointmentSlotAlreadyHeldError as exc:
        _commit_audit_best_effort(
            db,
            audit_logs,
            AuditLogCreate(
                event_type=AuditEventType.APPOINTMENT_HOLD_FAILED,
                outcome=AuditEventOutcome.FAILURE,
                actor_type=AuditActorType.API,
                source=SCHEDULING_API_SOURCE,
                actor_id=payload.owner_id,
                availability_slot_id=payload.availability_slot_id,
                event_metadata={"reason": "slot_already_held"},
            ),
        )
        raise APIError(
            status_code=status.HTTP_409_CONFLICT,
            code="appointment_slot_already_held",
            message="slot already has an active hold",
        ) from exc
    except (InvalidAppointmentHoldOwnerError, InvalidAppointmentHoldWindowError) as exc:
        _commit_audit_best_effort(
            db,
            audit_logs,
            AuditLogCreate(
                event_type=AuditEventType.APPOINTMENT_HOLD_FAILED,
                outcome=AuditEventOutcome.FAILURE,
                actor_type=AuditActorType.API,
                source=SCHEDULING_API_SOURCE,
                actor_id=payload.owner_id,
                availability_slot_id=payload.availability_slot_id,
                event_metadata={"reason": "invalid_appointment_hold"},
            ),
        )
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        ) from exc
    except AppointmentHoldStoreUnavailableError as exc:
        _commit_audit_best_effort(
            db,
            audit_logs,
            AuditLogCreate(
                event_type=AuditEventType.APPOINTMENT_HOLD_FAILED,
                outcome=AuditEventOutcome.FAILURE,
                actor_type=AuditActorType.API,
                source=SCHEDULING_API_SOURCE,
                actor_id=payload.owner_id,
                availability_slot_id=payload.availability_slot_id,
                event_metadata={"reason": "hold_store_unavailable"},
            ),
        )
        raise APIError(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            code="appointment_hold_store_unavailable",
            message="appointment hold store is temporarily unavailable",
        ) from exc

    _commit_audit_best_effort(
        db,
        audit_logs,
        AuditLogCreate(
            event_type=AuditEventType.APPOINTMENT_HOLD_CREATED,
            outcome=AuditEventOutcome.SUCCESS,
            actor_type=AuditActorType.API,
            source=SCHEDULING_API_SOURCE,
            actor_id=payload.owner_id,
            availability_slot_id=hold.availability_slot_id,
            event_metadata={
                "hold_id": str(hold.hold_id),
                "doctor_id": str(hold.doctor_id),
            },
        ),
    )

    return AppointmentHoldResponse(
        hold_id=hold.hold_id,
        availability_slot_id=hold.availability_slot_id,
        doctor_id=hold.doctor_id,
        start_time=hold.start_time,
        end_time=hold.end_time,
        owner_id=hold.owner_id,
        expires_in_seconds=hold_service.ttl_seconds,
    )


@router.post(
    "/appointments/book",
    response_model=AppointmentResponse,
    status_code=status.HTTP_201_CREATED,
)
def book_appointment(
    payload: AppointmentBookingRequestBody,
    db: Annotated[Session, Depends(get_db)],
    booking_service: Annotated[
        AppointmentBookingService,
        Depends(get_appointment_booking_service),
    ],
    hold_service: Annotated[AppointmentHoldService, Depends(get_appointment_hold_service)],
    audit_logs: Annotated[AuditLogService, Depends(get_audit_log_service)],
    email_jobs: Annotated[EmailJobService, Depends(get_email_job_service)],
    email_job_dispatch: Annotated[
        EmailJobDispatchPublisher,
        Depends(get_email_job_dispatch_publisher),
    ],
) -> Appointment:
    try:
        result = booking_service.book_appointment(
            AppointmentBookingRequest(
                hold_id=payload.hold_id,
                availability_slot_id=payload.availability_slot_id,
                patient_id=payload.patient_id,
                owner_id=payload.owner_id,
                reason=payload.reason,
            ),
        )
        appointment = result.appointment
        hold = result.hold

        audit_logs.record_best_effort(
            AuditLogCreate(
                event_type=AuditEventType.APPOINTMENT_BOOKING_CONFIRMED,
                outcome=AuditEventOutcome.SUCCESS,
                actor_type=AuditActorType.API,
                source=SCHEDULING_API_SOURCE,
                actor_id=payload.owner_id,
                patient_id=payload.patient_id,
                appointment_id=appointment.id,
                availability_slot_id=payload.availability_slot_id,
                event_metadata={"hold_id": str(payload.hold_id)},
            ),
        )

        email_job_result = email_jobs.get_or_create_appointment_confirmation_email_job(
            AppointmentConfirmationEmailJobCreate(
                appointment_id=appointment.id,
                patient_id=payload.patient_id,
                appointment_start_time=appointment.start_time.isoformat(),
                payload={
                    "source": "scheduling_api",
                    "hold_id": str(payload.hold_id),
                },
            )
        )
        email_job = email_job_result.email_job

        db.commit()
        db.refresh(appointment)

        try:
            email_job_dispatch.publish_email_job_ready(email_job_id=email_job.id)
        except EmailJobDispatchPublisherError:
            logger.warning(
                "email_job_dispatch_publish_failed",
                extra={
                    "event": "email_job_dispatch_publish_failed",
                    "email_job_id": str(email_job.id),
                    "appointment_id": str(appointment.id),
                },
            )

        hold_service.release_hold(
            doctor_id=hold.doctor_id,
            start_time=hold.start_time,
            owner_id=payload.owner_id,
        )

        return appointment
    except IntegrityError as exc:
        db.rollback()
        _commit_audit_best_effort(
            db,
            audit_logs,
            AuditLogCreate(
                event_type=AuditEventType.APPOINTMENT_BOOKING_FAILED,
                outcome=AuditEventOutcome.FAILURE,
                actor_type=AuditActorType.API,
                source=SCHEDULING_API_SOURCE,
                actor_id=payload.owner_id,
                patient_id=payload.patient_id,
                availability_slot_id=payload.availability_slot_id,
                event_metadata={
                    "hold_id": str(payload.hold_id),
                    "reason": "appointment_conflict",
                },
            ),
        )
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="appointment could not be booked because the slot is no longer available",
        ) from exc
    except BookingPatientNotFoundError as exc:
        db.rollback()
        _commit_audit_best_effort(
            db,
            audit_logs,
            AuditLogCreate(
                event_type=AuditEventType.APPOINTMENT_BOOKING_FAILED,
                outcome=AuditEventOutcome.FAILURE,
                actor_type=AuditActorType.API,
                source=SCHEDULING_API_SOURCE,
                actor_id=payload.owner_id,
                patient_id=payload.patient_id,
                availability_slot_id=payload.availability_slot_id,
                event_metadata={
                    "hold_id": str(payload.hold_id),
                    "reason": "patient_not_found",
                },
            ),
        )
        raise APIError(
            status_code=status.HTTP_404_NOT_FOUND,
            code="patient_not_found",
            message="patient was not found",
        ) from exc
    except BookingDoctorNotFoundError as exc:
        db.rollback()
        _commit_audit_best_effort(
            db,
            audit_logs,
            AuditLogCreate(
                event_type=AuditEventType.APPOINTMENT_BOOKING_FAILED,
                outcome=AuditEventOutcome.FAILURE,
                actor_type=AuditActorType.API,
                source=SCHEDULING_API_SOURCE,
                actor_id=payload.owner_id,
                patient_id=payload.patient_id,
                availability_slot_id=payload.availability_slot_id,
                event_metadata={
                    "hold_id": str(payload.hold_id),
                    "reason": "doctor_not_found",
                },
            ),
        )
        raise APIError(
            status_code=status.HTTP_404_NOT_FOUND,
            code="doctor_not_found",
            message="doctor was not found or is inactive",
        ) from exc
    except BookingAvailabilitySlotNotFoundError as exc:
        db.rollback()
        _commit_audit_best_effort(
            db,
            audit_logs,
            AuditLogCreate(
                event_type=AuditEventType.APPOINTMENT_BOOKING_FAILED,
                outcome=AuditEventOutcome.FAILURE,
                actor_type=AuditActorType.API,
                source=SCHEDULING_API_SOURCE,
                actor_id=payload.owner_id,
                patient_id=payload.patient_id,
                availability_slot_id=payload.availability_slot_id,
                event_metadata={
                    "hold_id": str(payload.hold_id),
                    "reason": "availability_slot_not_found",
                },
            ),
        )
        raise APIError(
            status_code=status.HTTP_404_NOT_FOUND,
            code="availability_slot_not_found",
            message="availability slot was not found",
        ) from exc
    except BookingAvailabilitySlotUnavailableError as exc:
        db.rollback()
        _commit_audit_best_effort(
            db,
            audit_logs,
            AuditLogCreate(
                event_type=AuditEventType.APPOINTMENT_BOOKING_FAILED,
                outcome=AuditEventOutcome.FAILURE,
                actor_type=AuditActorType.API,
                source=SCHEDULING_API_SOURCE,
                actor_id=payload.owner_id,
                patient_id=payload.patient_id,
                availability_slot_id=payload.availability_slot_id,
                event_metadata={
                    "hold_id": str(payload.hold_id),
                    "reason": "availability_slot_unavailable",
                },
            ),
        )
        raise APIError(
            status_code=status.HTTP_409_CONFLICT,
            code="availability_slot_unavailable",
            message="availability slot is not available",
        ) from exc
    except AppointmentSlotAlreadyBookedError as exc:
        db.rollback()
        _commit_audit_best_effort(
            db,
            audit_logs,
            AuditLogCreate(
                event_type=AuditEventType.APPOINTMENT_BOOKING_FAILED,
                outcome=AuditEventOutcome.FAILURE,
                actor_type=AuditActorType.API,
                source=SCHEDULING_API_SOURCE,
                actor_id=payload.owner_id,
                patient_id=payload.patient_id,
                availability_slot_id=payload.availability_slot_id,
                event_metadata={
                    "hold_id": str(payload.hold_id),
                    "reason": "slot_already_booked",
                },
            ),
        )
        raise APIError(
            status_code=status.HTTP_409_CONFLICT,
            code="appointment_slot_already_booked",
            message="doctor already has a scheduled appointment at this time",
        ) from exc
    except AppointmentBookingOwnerRequiredError as exc:
        db.rollback()
        _commit_audit_best_effort(
            db,
            audit_logs,
            AuditLogCreate(
                event_type=AuditEventType.APPOINTMENT_BOOKING_FAILED,
                outcome=AuditEventOutcome.FAILURE,
                actor_type=AuditActorType.API,
                source=SCHEDULING_API_SOURCE,
                actor_id=payload.owner_id,
                patient_id=payload.patient_id,
                availability_slot_id=payload.availability_slot_id,
                event_metadata={
                    "hold_id": str(payload.hold_id),
                    "reason": "missing_booking_owner",
                },
            ),
        )
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="owner_id is required",
        ) from exc
    except AppointmentHoldNotFoundError as exc:
        db.rollback()
        _commit_audit_best_effort(
            db,
            audit_logs,
            AuditLogCreate(
                event_type=AuditEventType.APPOINTMENT_BOOKING_FAILED,
                outcome=AuditEventOutcome.FAILURE,
                actor_type=AuditActorType.API,
                source=SCHEDULING_API_SOURCE,
                actor_id=payload.owner_id,
                patient_id=payload.patient_id,
                availability_slot_id=payload.availability_slot_id,
                event_metadata={
                    "hold_id": str(payload.hold_id),
                    "reason": "appointment_hold_expired",
                },
            ),
        )
        raise APIError(
            status_code=status.HTTP_409_CONFLICT,
            code="appointment_hold_not_found",
            message="appointment hold was not found or expired",
        ) from exc
    except AppointmentHoldMismatchError as exc:
        db.rollback()
        _commit_audit_best_effort(
            db,
            audit_logs,
            AuditLogCreate(
                event_type=AuditEventType.APPOINTMENT_BOOKING_FAILED,
                outcome=AuditEventOutcome.FAILURE,
                actor_type=AuditActorType.API,
                source=SCHEDULING_API_SOURCE,
                actor_id=payload.owner_id,
                patient_id=payload.patient_id,
                availability_slot_id=payload.availability_slot_id,
                event_metadata={
                    "hold_id": str(payload.hold_id),
                    "reason": "appointment_hold_mismatch",
                },
            ),
        )
        raise APIError(
            status_code=status.HTTP_409_CONFLICT,
            code="appointment_hold_mismatch",
            message="appointment hold id does not match",
        ) from exc
    except AppointmentHoldOwnershipError as exc:
        db.rollback()
        _commit_audit_best_effort(
            db,
            audit_logs,
            AuditLogCreate(
                event_type=AuditEventType.APPOINTMENT_BOOKING_FAILED,
                outcome=AuditEventOutcome.FAILURE,
                actor_type=AuditActorType.API,
                source=SCHEDULING_API_SOURCE,
                actor_id=payload.owner_id,
                patient_id=payload.patient_id,
                availability_slot_id=payload.availability_slot_id,
                event_metadata={
                    "hold_id": str(payload.hold_id),
                    "reason": "appointment_hold_owner_mismatch",
                },
            ),
        )
        raise APIError(
            status_code=status.HTTP_403_FORBIDDEN,
            code="appointment_hold_ownership_error",
            message="appointment hold belongs to another owner",
        ) from exc
