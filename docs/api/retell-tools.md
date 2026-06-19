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

Retell tool endpoints return structured tool responses.

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

Transport or validation errors may still return HTTP 4xx.

## Read Tools

### list_specialties

    POST /api/v1/retell/tools/list-specialties

Request body:

    {}

Returns supported clinic specialties.

### list_doctors

    POST /api/v1/retell/tools/list-doctors

Request body:

    {
      "specialty_name": "Dermatology",
      "doctor_name": "Dr Carter"
    }

Both fields are optional.

The backend resolves human-friendly names to real doctors.

### check_availability

    POST /api/v1/retell/tools/check-availability

Request body using doctor name:

    {
      "doctor_name": "Dr Carter",
      "start_from": "2026-07-01T09:00:00Z",
      "start_to": "2026-07-01T12:00:00Z"
    }

Request body using doctor ID:

    {
      "doctor_id": "7fcf0ca1-7f14-4f45-a8a4-cc77d1a67f4d",
      "start_from": "2026-07-01T09:00:00Z",
      "start_to": "2026-07-01T12:00:00Z"
    }

Retell may use a doctor ID returned by `list_doctors`.

The patient should not hear internal IDs.

### lookup_patient

    POST /api/v1/retell/tools/lookup-patient

Request body:

    {
      "full_name": "John Miller",
      "date_of_birth": "1985-04-12",
      "phone_number": "+1-555-0201"
    }

Patient lookup requires:

- Full name
- Date of birth
- Phone number or email

Full name and date of birth alone are intentionally insufficient.

### list_upcoming_appointments

    POST /api/v1/retell/tools/list-upcoming-appointments

Request body:

    {
      "full_name": "John Miller",
      "date_of_birth": "1985-04-12",
      "email": "john.miller@example.test",
      "start_from": "2026-07-01T09:00:00Z"
    }

Returns scheduled upcoming appointments after identity validation.

## Hold Tools

### hold_appointment_slot

    POST /api/v1/retell/tools/hold-appointment-slot

Request body:

    {
      "availability_slot_id": "7fcf0ca1-7f14-4f45-a8a4-cc77d1a67f4d",
      "call_id": "retell-call-123"
    }

Retell should call this tool after the patient selects a specific availability slot.

The backend returns a hold_id that must be used later during final booking.

Ownership can be provided through:

- call_id
- conversation_id
- owner_id

The backend requires one of these identifiers so another call or conversation cannot use the hold.

Example success response:

    {
      "ok": true,
      "result": {
        "hold_id": "2d85f2c2-5d2e-4c2a-ae2f-09e32011ce37",
        "availability_slot_id": "7fcf0ca1-7f14-4f45-a8a4-cc77d1a67f4d",
        "doctor_id": "c72f20fd-71a3-42e4-9611-6bca3c7d44c5",
        "start_time": "2026-07-01T10:00:00+00:00",
        "end_time": "2026-07-01T10:30:00+00:00",
        "expires_in_seconds": 300
      }
    }

## Current Limitations

These tools do not implement:

- Final appointment booking
- Appointment rescheduling
- Appointment cancellation
- Audit logs
- RabbitMQ email jobs
- Retell webhook signature validation
- Real Retell dashboard configuration

## Future Work

Future implementation phases should add:

- Booking tool endpoint
- Reschedule tool endpoint
- Cancellation tool endpoint
- Escalation case tool endpoint
- Audit logs for tool calls
- Retell prompt/runtime configuration documentation
- Webhook validation and production hardening