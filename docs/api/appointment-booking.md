# Appointment Booking API

## Context

Appointment booking transforms a temporary Redis hold into a durable PostgreSQL appointment.

The booking API is called after:

1. Availability has been checked.
2. The patient selected a slot.
3. The backend created an appointment hold.
4. The receptionist, chat agent, or Retell voice agent confirmed the details.
5. The patient gave final confirmation.

## Generic Booking Endpoint

    POST /api/v1/scheduling/appointments/book

Request body:

    {
      "hold_id": "2d85f2c2-5d2e-4c2a-ae2f-09e32011ce37",
      "availability_slot_id": "7fcf0ca1-7f14-4f45-a8a4-cc77d1a67f4d",
      "patient_id": "4c71ec24-892b-4ab1-b4f4-cf5e42e88e91",
      "owner_id": "chat-conversation-123",
      "reason": "Skin check"
    }

Successful response:

    {
      "id": "52be5d4b-c874-46df-b67e-64ebff728a67",
      "patient_id": "4c71ec24-892b-4ab1-b4f4-cf5e42e88e91",
      "doctor_id": "c72f20fd-71a3-42e4-9611-6bca3c7d44c5",
      "specialty_id": "b5df6951-f1d3-42cb-8a61-1243654c3465",
      "availability_slot_id": "7fcf0ca1-7f14-4f45-a8a4-cc77d1a67f4d",
      "rescheduled_from_appointment_id": null,
      "start_time": "2026-07-01T10:00:00Z",
      "end_time": "2026-07-01T10:30:00Z",
      "status": "scheduled",
      "reason": "Skin check",
      "cancellation_reason": null,
      "cancelled_at": null
    }

## Retell Booking Tool

    POST /api/v1/retell/tools/book-appointment

Request body:

    {
      "hold_id": "2d85f2c2-5d2e-4c2a-ae2f-09e32011ce37",
      "availability_slot_id": "7fcf0ca1-7f14-4f45-a8a4-cc77d1a67f4d",
      "patient_id": "4c71ec24-892b-4ab1-b4f4-cf5e42e88e91",
      "call_id": "retell-call-123",
      "reason": "Skin check"
    }

Retell may provide ownership through:

- call_id
- conversation_id
- owner_id

The backend requires one of them.

## Transaction Behavior

The booking endpoint:

1. Validates the Redis hold.
2. Creates the appointment in PostgreSQL.
3. Marks the availability slot as booked.
4. Commits the database transaction.
5. Releases the Redis hold after the commit succeeds.

Redis and PostgreSQL do not participate in a single distributed transaction.

PostgreSQL remains the final durable consistency layer.

## Error Behavior

The generic API uses HTTP status codes.

Examples:

- 404 when the patient, doctor, or slot is not found
- 409 when the slot is unavailable, booked, expired, or conflicting
- 403 when the hold belongs to another owner
- 400 when owner_id is missing

The Retell endpoint returns structured tool responses so the voice agent can continue naturally:

    {
      "ok": false,
      "error_code": "appointment_hold_expired",
      "message": "The temporary hold was not found or has expired."
    }

## Current Limitations

This implementation does not yet include:

- Patient creation during booking
- Audit logs
- RabbitMQ confirmation email jobs
- Retell dashboard configuration
- Webhook signature validation
- Full concurrency simulation against PostgreSQL