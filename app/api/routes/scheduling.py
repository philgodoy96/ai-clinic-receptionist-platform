from collections.abc import Sequence
from datetime import datetime
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status

from app.api.dependencies import get_scheduling_service
from app.models.scheduling import Appointment, AvailabilitySlot, Doctor, Patient, Specialty
from app.schemas.scheduling import (
    AppointmentResponse,
    AvailabilitySlotResponse,
    DoctorResponse,
    PatientLookupRequest,
    PatientResponse,
    SpecialtyResponse,
    UpcomingAppointmentsRequest,
)
from app.services.scheduling import (
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