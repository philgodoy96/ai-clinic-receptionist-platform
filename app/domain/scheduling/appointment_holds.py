from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID, uuid4


@dataclass(frozen=True, slots=True)
class AppointmentHold:
    hold_id: UUID
    availability_slot_id: UUID
    doctor_id: UUID
    start_time: datetime
    end_time: datetime
    owner_id: str
    created_at: datetime

    @classmethod
    def create(
        cls,
        *,
        availability_slot_id: UUID,
        doctor_id: UUID,
        start_time: datetime,
        end_time: datetime,
        owner_id: str,
    ) -> "AppointmentHold":
        return cls(
            hold_id=uuid4(),
            availability_slot_id=availability_slot_id,
            doctor_id=doctor_id,
            start_time=start_time,
            end_time=end_time,
            owner_id=owner_id,
            created_at=datetime.now(UTC),
        )