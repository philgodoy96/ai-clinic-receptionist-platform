# Retell Tool API

## Context

Retell orchestrates the real-time voice experience.

The backend exposes tools that Retell can call when it needs real clinic data, validation, persistence, or side effects.

## Design Principle

Retell is not the business logic core.

Retell handles voice UX.

The backend handles:

- Tool validation
- Patient data protection
- Scheduling rules
- Persistence
- Auditability
- Observability
- Future side effects

## Base Path

    /api/v1/retell/tools

## Response Shape

Successful response:

    {
      "ok": true,
      "result": {}
    }

Business error response:

    {
      "ok": false,
      "error_code": "doctor_not_found",
      "message": "No matching doctor was found."
    }

Business errors usually return HTTP 200 so the voice agent can continue the conversation naturally.

## Read Tools

### list_specialties

    POST /api/v1/retell/tools/list-specialties

### list_doctors

    POST /api/v1/retell/tools/list-doctors

Example request:

    {
      "specialty_name": "Dermatology",
      "doctor_name": "Dr Carter"
    }

### check_availability

    POST /api/v1/retell/tools/check-availability

Example request:

    {
      "doctor_name": "Dr Carter",
      "start_from": "2026-07-01T09:00:00Z",
      "start_to": "2026-07-01T12:00:00Z"
    }

### lookup_patient

    POST /api/v1/retell/tools/lookup-patient

Example request:

    {
      "full_name": "John Miller",
      "date_of_birth": "1985-04-12",
      "phone_number": "+1-555-0201"
    }

### list_upcoming_appointments

    POST /api/v1/retell/tools/list-upcoming-appointments

Example request:

    {
      "full_name": "John Miller",
      "date_of_birth": "1985-04-12",
      "email": "john.miller@example.test",
      "start_from": "2026-07-01T09:00:00Z"
    }

## Hold Tools

### hold_appointment_slot

    POST /api/v1/retell/tools/hold-appointment-slot

Example request:

    {
      "availability_slot_id": "7fcf0ca1-7f14-4f45-a8a4-cc77d1a67f4d",
      "call_id": "retell-call-123"
    }

Retell should call this tool after the patient selects a specific availability slot.

The backend returns a hold_id that must be used later during final booking.

## Booking Tools

### book_appointment

    POST /api/v1/retell/tools/book-appointment

Example request:

    {
      "hold_id": "2d85f2c2-5d2e-4c2a-ae2f-09e32011ce37",
      "availability_slot_id": "7fcf0ca1-7f14-4f45-a8a4-cc77d1a67f4d",
      "patient_id": "4c71ec24-892b-4ab1-b4f4-cf5e42e88e91",
      "call_id": "retell-call-123",
      "reason": "Skin check"
    }

Retell should call this tool only after the patient gives final confirmation.

The backend validates the Redis hold, writes the appointment to PostgreSQL, commits the transaction, and releases the hold after the commit succeeds.

## Current Limitations

These tools do not implement:

- Appointment rescheduling
- Appointment cancellation
- Audit logs
- RabbitMQ email jobs
- Retell webhook signature validation
- Real Retell dashboard configuration

## Future Work

Future implementation phases should add:

- Reschedule tool endpoint
- Cancellation tool endpoint
- Escalation case tool endpoint
- Audit logs for tool calls
- Email confirmation jobs
- Retell prompt/runtime configuration documentation
- Webhook validation and production hardening