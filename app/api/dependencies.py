from typing import Annotated

from fastapi import Depends
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.repositories.sqlalchemy.scheduling import (
    SQLAlchemyAppointmentRepository,
    SQLAlchemyAvailabilitySlotRepository,
    SQLAlchemyDoctorRepository,
    SQLAlchemyPatientRepository,
    SQLAlchemySpecialtyRepository,
)
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