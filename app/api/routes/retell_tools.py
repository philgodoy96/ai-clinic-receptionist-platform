from typing import Annotated

from fastapi import APIRouter, Depends

from app.adapters.retell.appointment_hold_tools import RetellAppointmentHoldToolAdapter
from app.adapters.retell.scheduling_tools import RetellSchedulingToolAdapter
from app.api.dependencies import (
    get_retell_appointment_hold_tool_adapter,
    get_retell_scheduling_tool_adapter,
)
from app.schemas.retell_tools import (
    RetellCheckAvailabilityRequest,
    RetellHoldAppointmentSlotRequest,
    RetellListDoctorsRequest,
    RetellListSpecialtiesRequest,
    RetellPatientLookupRequest,
    RetellToolResponse,
    RetellUpcomingAppointmentsRequest,
)

router = APIRouter(prefix="/api/v1/retell/tools", tags=["retell-tools"])


@router.post("/list-specialties", response_model=RetellToolResponse)
def list_specialties(
    payload: RetellListSpecialtiesRequest,
    adapter: Annotated[
        RetellSchedulingToolAdapter,
        Depends(get_retell_scheduling_tool_adapter),
    ],
) -> RetellToolResponse:
    return adapter.list_specialties()


@router.post("/list-doctors", response_model=RetellToolResponse)
def list_doctors(
    payload: RetellListDoctorsRequest,
    adapter: Annotated[
        RetellSchedulingToolAdapter,
        Depends(get_retell_scheduling_tool_adapter),
    ],
) -> RetellToolResponse:
    return adapter.list_doctors(payload)


@router.post("/check-availability", response_model=RetellToolResponse)
def check_availability(
    payload: RetellCheckAvailabilityRequest,
    adapter: Annotated[
        RetellSchedulingToolAdapter,
        Depends(get_retell_scheduling_tool_adapter),
    ],
) -> RetellToolResponse:
    return adapter.check_availability(payload)


@router.post("/lookup-patient", response_model=RetellToolResponse)
def lookup_patient(
    payload: RetellPatientLookupRequest,
    adapter: Annotated[
        RetellSchedulingToolAdapter,
        Depends(get_retell_scheduling_tool_adapter),
    ],
) -> RetellToolResponse:
    return adapter.lookup_patient(payload)


@router.post("/list-upcoming-appointments", response_model=RetellToolResponse)
def list_upcoming_appointments(
    payload: RetellUpcomingAppointmentsRequest,
    adapter: Annotated[
        RetellSchedulingToolAdapter,
        Depends(get_retell_scheduling_tool_adapter),
    ],
) -> RetellToolResponse:
    return adapter.list_upcoming_appointments(payload)


@router.post("/hold-appointment-slot", response_model=RetellToolResponse)
def hold_appointment_slot(
    payload: RetellHoldAppointmentSlotRequest,
    adapter: Annotated[
        RetellAppointmentHoldToolAdapter,
        Depends(get_retell_appointment_hold_tool_adapter),
    ],
) -> RetellToolResponse:
    return adapter.hold_appointment_slot(payload)