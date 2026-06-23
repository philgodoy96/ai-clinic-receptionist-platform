from enum import StrEnum


class AvailabilitySlotStatus(StrEnum):
    AVAILABLE = "available"
    HELD = "held"
    BOOKED = "booked"
    BLOCKED = "blocked"


class AppointmentStatus(StrEnum):
    SCHEDULED = "scheduled"
    RESCHEDULED = "rescheduled"
    CANCELLED = "cancelled"
    COMPLETED = "completed"
