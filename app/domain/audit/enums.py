from enum import StrEnum


class AuditEventType(StrEnum):
    APPOINTMENT_HOLD_CREATED = "appointment_hold_created"
    APPOINTMENT_HOLD_FAILED = "appointment_hold_failed"
    APPOINTMENT_BOOKING_CONFIRMED = "appointment_booking_confirmed"
    APPOINTMENT_BOOKING_FAILED = "appointment_booking_failed"
    APPOINTMENT_CANCELLATION_CONFIRMED = "appointment_cancellation_confirmed"
    APPOINTMENT_CANCELLATION_FAILED = "appointment_cancellation_failed"
    APPOINTMENT_RESCHEDULE_REQUESTED = "appointment_reschedule_requested"
    APPOINTMENT_RESCHEDULE_SUCCEEDED = "appointment_reschedule_succeeded"
    APPOINTMENT_RESCHEDULE_REJECTED = "appointment_reschedule_rejected"
    APPOINTMENT_RESCHEDULE_DUPLICATE = "appointment_reschedule_duplicate"


class AuditActorType(StrEnum):
    SYSTEM = "system"
    PATIENT = "patient"
    RETELL = "retell"
    CHAT = "chat"
    API = "api"


class AuditEventOutcome(StrEnum):
    SUCCESS = "success"
    FAILURE = "failure"
