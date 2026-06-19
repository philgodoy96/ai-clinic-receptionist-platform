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
    get_scheduling_service,
)
from app.db.session import get_db
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
    AppointmentSlotAlreadyHeldError,
    InvalidAppointmentHoldOwnerError,
    InvalidAppointmentHoldWindowError,
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
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="start_to must be greater than start_from",
        ) from exc
    except DoctorNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="doctor was not found or is inactive",
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
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="patient lookup requires phone_number or email",
        ) from exc

    if patient is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="patient was not found",
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
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="patient lookup requires phone_number or email",
        ) from exc


@router.post(
    "/appointment-holds",
    response_model=AppointmentHoldResponse,
    status_code=status.HTTP_201_CREATED,
)
def hold_appointment_slot(
    payload: AppointmentHoldCreateRequest,
    scheduling_service: Annotated[SchedulingService, Depends(get_scheduling_service)],
    hold_service: Annotated[AppointmentHoldService, Depends(get_appointment_hold_service)],
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
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="availability slot was not found",
        ) from exc
    except AvailabilitySlotUnavailableError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="availability slot is not available",
        ) from exc
    except AppointmentSlotAlreadyHeldError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="slot already has an active hold",
        ) from exc
    except (InvalidAppointmentHoldOwnerError, InvalidAppointmentHoldWindowError) as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        ) from exc

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

        db.commit()
        db.refresh(appointment)

        hold_service.release_hold(
            doctor_id=hold.doctor_id,
            start_time=hold.start_time,
            owner_id=payload.owner_id,
        )

        return appointment
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="appointment could not be booked because the slot is no longer available",
        ) from exc
    except BookingPatientNotFoundError as exc:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="patient was not found",
        ) from exc
    except BookingDoctorNotFoundError as exc:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="doctor was not found or is inactive",
        ) from exc
    except BookingAvailabilitySlotNotFoundError as exc:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="availability slot was not found",
        ) from exc
    except BookingAvailabilitySlotUnavailableError as exc:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="availability slot is not available",
        ) from exc
    except AppointmentSlotAlreadyBookedError as exc:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="doctor already has a scheduled appointment at this time",
        ) from exc
    except AppointmentBookingOwnerRequiredError as exc:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="owner_id is required",
        ) from exc
    except AppointmentHoldNotFoundError as exc:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="appointment hold was not found or expired",
        ) from exc
    except AppointmentHoldMismatchError as exc:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="appointment hold id does not match",
        ) from exc
    except AppointmentHoldOwnershipError as exc:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="appointment hold belongs to another owner",
        ) from exc