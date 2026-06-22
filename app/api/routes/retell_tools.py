from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends

from app.adapters.retell.appointment_booking_tools import RetellAppointmentBookingToolAdapter
from app.adapters.retell.appointment_hold_tools import RetellAppointmentHoldToolAdapter
from app.adapters.retell.scheduling_tools import RetellSchedulingToolAdapter
from app.api.demo_guardrail_enforcement import require_retell_tool_guardrail
from app.api.dependencies import (
    get_retell_appointment_booking_tool_adapter,
    get_retell_appointment_hold_tool_adapter,
    get_retell_scheduling_tool_adapter,
)
from app.api.retell_webhook_security import require_retell_webhook_security, retell_tool_payload
from app.schemas.retell_tools import (
    RetellBookAppointmentRequest,
    RetellCheckAvailabilityRequest,
    RetellHoldAppointmentSlotRequest,
    RetellListDoctorsRequest,
    RetellListSpecialtiesRequest,
    RetellPatientLookupRequest,
    RetellToolResponse,
    RetellUpcomingAppointmentsRequest,
)

router = APIRouter(
    prefix="/api/v1/retell/tools",
    tags=["retell-tools"],
    dependencies=[
        Depends(require_retell_webhook_security),
        Depends(require_retell_tool_guardrail),
    ],
)


@router.post("/list-specialties", response_model=RetellToolResponse)
def list_specialties(
    payload: Annotated[
        RetellListSpecialtiesRequest,
        Depends(retell_tool_payload(RetellListSpecialtiesRequest)),
    ],
    adapter: Annotated[
        RetellSchedulingToolAdapter,
        Depends(get_retell_scheduling_tool_adapter),
    ],
) -> RetellToolResponse:
    return adapter.list_specialties()


@router.post("/list-doctors", response_model=RetellToolResponse)
def list_doctors(
    payload: Annotated[
        RetellListDoctorsRequest,
        Depends(retell_tool_payload(RetellListDoctorsRequest)),
    ],
    adapter: Annotated[
        RetellSchedulingToolAdapter,
        Depends(get_retell_scheduling_tool_adapter),
    ],
) -> RetellToolResponse:
    return adapter.list_doctors(payload)


@router.post("/check-availability", response_model=RetellToolResponse)
def check_availability(
    payload: Annotated[
        RetellCheckAvailabilityRequest,
        Depends(retell_tool_payload(RetellCheckAvailabilityRequest)),
    ],
    adapter: Annotated[
        RetellSchedulingToolAdapter,
        Depends(get_retell_scheduling_tool_adapter),
    ],
) -> RetellToolResponse:
    return adapter.check_availability(payload)


@router.post("/lookup-patient", response_model=RetellToolResponse)
def lookup_patient(
    payload: Annotated[
        RetellPatientLookupRequest,
        Depends(retell_tool_payload(RetellPatientLookupRequest)),
    ],
    adapter: Annotated[
        RetellSchedulingToolAdapter,
        Depends(get_retell_scheduling_tool_adapter),
    ],
) -> RetellToolResponse:
    return adapter.lookup_patient(payload)


@router.post("/list-upcoming-appointments", response_model=RetellToolResponse)
def list_upcoming_appointments(
    payload: Annotated[
        RetellUpcomingAppointmentsRequest,
        Depends(retell_tool_payload(RetellUpcomingAppointmentsRequest)),
    ],
    adapter: Annotated[
        RetellSchedulingToolAdapter,
        Depends(get_retell_scheduling_tool_adapter),
    ],
) -> RetellToolResponse:
    return adapter.list_upcoming_appointments(payload)


@router.post("/hold-appointment-slot", response_model=RetellToolResponse)
def hold_appointment_slot(
    payload: Annotated[
        RetellHoldAppointmentSlotRequest,
        Depends(retell_tool_payload(RetellHoldAppointmentSlotRequest)),
    ],
    adapter: Annotated[
        RetellAppointmentHoldToolAdapter,
        Depends(get_retell_appointment_hold_tool_adapter),
    ],
) -> RetellToolResponse:
    return adapter.hold_appointment_slot(payload)


@router.post("/book-appointment", response_model=RetellToolResponse)
def book_appointment(
    payload: Annotated[
        RetellBookAppointmentRequest,
        Depends(retell_tool_payload(RetellBookAppointmentRequest)),
    ],
    adapter: Annotated[
        RetellAppointmentBookingToolAdapter,
        Depends(get_retell_appointment_booking_tool_adapter),
    ],
) -> RetellToolResponse:
    return adapter.book_appointment(payload)
