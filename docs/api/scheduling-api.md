# Scheduling Read API

## Context

This API exposes read-only scheduling capabilities for the AI Clinic Receptionist Platform.

These endpoints are useful for:

- Local API testing
- Demo UI integration
- Future chat channel orchestration
- Future Retell tool adapter implementation

This API does not book, reschedule, or cancel appointments yet.

## Base Path

    /api/v1/scheduling

## Endpoints

### List Specialties

    GET /api/v1/scheduling/specialties

Returns active clinic specialties.

Example response:

    [
      {
        "id": "7fcf0ca1-7f14-4f45-a8a4-cc77d1a67f4d",
        "name": "Primary Care",
        "description": "General health visits",
        "is_active": true
      }
    ]

### List Doctors

    GET /api/v1/scheduling/doctors

Optional query parameter:

    specialty_id

Example:

    GET /api/v1/scheduling/doctors?specialty_id=7fcf0ca1-7f14-4f45-a8a4-cc77d1a67f4d

Returns active doctors.

### Check Doctor Availability

    GET /api/v1/scheduling/doctors/{doctor_id}/availability

Required query parameters:

    start_from
    start_to

Example:

    GET /api/v1/scheduling/doctors/{doctor_id}/availability?start_from=2026-07-01T09:00:00Z&start_to=2026-07-01T12:00:00Z

Returns available slots for a doctor within the requested time window.

Availability results are filtered by scheduling policy:

- Minimum booking lead time (`SCHEDULING_MIN_BOOKING_LEAD_MINUTES`, default 60)
- Booking horizon (`SCHEDULING_BOOKING_HORIZON_DAYS`, default 14)
- Durable slot status (`available` only)
- Active Redis holds when Redis is available

Availability is an advisory read. It does not guarantee a slot can still be held or booked.

See [Scheduling Application Services](../architecture/scheduling-services.md#scheduling-availability-policy).

Validation:

- `start_to` must be greater than `start_from`.
- Doctor must exist and be active.

### Lookup Patient

    POST /api/v1/scheduling/patients/lookup

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

### List Upcoming Patient Appointments

    POST /api/v1/scheduling/patients/upcoming-appointments

Request body:

    {
      "full_name": "John Miller",
      "date_of_birth": "1985-04-12",
      "email": "john.miller@example.test",
      "start_from": "2026-07-01T09:00:00Z"
    }

Returns scheduled upcoming appointments for a patient after sufficient identity validation.

If the patient is not found, the endpoint returns an empty list.

## Security Notes

This API is unauthenticated in the local/demo version.

Even without authentication, the patient-related endpoints enforce a basic identity rule:

    full_name + date_of_birth + phone_number or email

Future production hardening should add authentication, rate limiting, audit logging, and stricter patient data access controls.

## Current Limitations

This API does not implement:

- Appointment booking
- Appointment rescheduling
- Appointment cancellation
- Redis appointment holds
- Audit logs
- Retell-specific tool payloads
- Chat orchestration
- RabbitMQ email confirmation jobs

Those capabilities will be added in later slices.