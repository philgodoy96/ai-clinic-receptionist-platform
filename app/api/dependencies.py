from typing import Annotated, Any

from fastapi import Depends
from sqlalchemy.orm import Session

from app.adapters.retell.appointment_hold_tools import RetellAppointmentHoldToolAdapter
from app.adapters.retell.scheduling_tools import RetellSchedulingToolAdapter
from app.cache.redis import get_redis_client
from app.core.config import get_settings
from app.db.session import get_db
from app.repositories.redis.appointment_holds import RedisAppointmentHoldRepository
from app.repositories.sqlalchemy.scheduling import (
    SQLAlchemyAppointmentRepository,
    SQLAlchemyAvailabilitySlotRepository,
    SQLAlchemyDoctorRepository,
    SQLAlchemyPatientRepository,
    SQLAlchemySpecialtyRepository,
)
from app.services.appointment_holds import AppointmentHoldService
from app.services.scheduling import SchedulingService


def get_scheduling_service(
    db: Annotated[Session, Depends(get_db)],
) -> SchedulingService:
    return SchedulingService(
        specialties=SQLAlchemySpecialtyRepository(db),
        doctors=SQLAlchemyDoctorRepository(db),
        patients=SQLAlchemyPatientRepository(db),
        availability_slots=SQLAlchemyAvailabilitySlotRepository(db),
        appointments=SQLAlchemyAppointmentRepository(db),
    )


def get_appointment_hold_service(
    redis_client: Annotated[Any, Depends(get_redis_client)],
) -> AppointmentHoldService:
    settings = get_settings()

    return AppointmentHoldService(
        repository=RedisAppointmentHoldRepository(redis_client),
        ttl_seconds=settings.appointment_hold_ttl_seconds,
    )


def get_retell_scheduling_tool_adapter(
    service: Annotated[SchedulingService, Depends(get_scheduling_service)],
) -> RetellSchedulingToolAdapter:
    return RetellSchedulingToolAdapter(service)


def get_retell_appointment_hold_tool_adapter(
    scheduling_service: Annotated[SchedulingService, Depends(get_scheduling_service)],
    hold_service: Annotated[AppointmentHoldService, Depends(get_appointment_hold_service)],
) -> RetellAppointmentHoldToolAdapter:
    return RetellAppointmentHoldToolAdapter(
        scheduling_service=scheduling_service,
        hold_service=hold_service,
    )