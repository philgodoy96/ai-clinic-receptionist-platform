# Retell Read Tool API

## Context

Retell orchestrates the real-time voice experience.

The backend exposes tools that Retell can call when it needs real clinic data, validation, persistence, or side effects.

This document describes the first read-only Retell tool endpoints.

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

## Tools

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

This allows Retell to handle phrases such as:

- "I need a dermatologist"
- "Can I see Dr. Carter?"
- "Who do you have for primary care?"

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

IDs are operational data exchanged between Retell and the backend.

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

## Current Limitations

These tools are read-only.

They do not implement:

- Appointment slot holds
- Appointment booking
- Appointment rescheduling
- Appointment cancellation
- Audit logs
- RabbitMQ email jobs
- Retell webhook signature validation
- Real Retell dashboard configuration

## Future Work

Future slices should add:

- Redis appointment slot holds
- Booking tool endpoint
- Reschedule tool endpoint
- Cancellation tool endpoint
- Escalation case tool endpoint
- Audit logs for tool calls
- Retell prompt/runtime configuration documentation
- Webhook validation and production hardening