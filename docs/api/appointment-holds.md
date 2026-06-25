# Appointment Hold API

## Context

Appointment holds temporarily reserve an available appointment slot before final booking.

This protects the user experience by preventing multiple callers or conversations from holding the same doctor/time slot at the same time.

Final booking is implemented separately.

## Generic API Endpoint

    POST /api/v1/scheduling/appointment-holds

Request body:

    {
      "availability_slot_id": "7fcf0ca1-7f14-4f45-a8a4-cc77d1a67f4d",
      "owner_id": "chat-conversation-123"
    }

Response body:

    {
      "hold_id": "2d85f2c2-5d2e-4c2a-ae2f-09e32011ce37",
      "availability_slot_id": "7fcf0ca1-7f14-4f45-a8a4-cc77d1a67f4d",
      "doctor_id": "c72f20fd-71a3-42e4-9611-6bca3c7d44c5",
      "start_time": "2026-07-01T10:00:00Z",
      "end_time": "2026-07-01T10:30:00Z",
      "owner_id": "chat-conversation-123",
      "expires_in_seconds": 300
    }

## Retell Tool Endpoint

    POST /api/v1/retell/tools/hold-appointment-slot

Request body:

    {
      "availability_slot_id": "7fcf0ca1-7f14-4f45-a8a4-cc77d1a67f4d",
      "call_id": "retell-call-123"
    }

Retell may provide ownership through:

- call_id
- conversation_id
- owner_id

The backend requires one of them.

## Validation

The backend validates:

- Availability slot exists
- Availability slot is currently available
- Owner identifier is present
- Slot does not already have an active Redis hold

## Error Behavior

The generic scheduling endpoint uses HTTP status codes.

Examples:

- `404` when the availability slot does not exist
- `409` when the slot is unavailable or already held
- `400` when the hold request is invalid
- `503` with `appointment_hold_store_unavailable` when Redis cannot create the hold (fail closed)

The Retell endpoint returns structured tool responses so the voice agent can continue naturally:

    {
      "ok": false,
      "error_code": "slot_already_held",
      "message": "The selected slot is already being held."
    }

When Redis is unavailable:

    {
      "ok": false,
      "error_code": "appointment_hold_store_unavailable",
      "message": "The appointment hold service is temporarily unavailable."
    }

Redis is required for hold creation. Availability lookup may still return candidate slots when Redis hold filtering is unavailable; see [Scheduling Application Services](../architecture/scheduling-services.md#redis-degradation-policy).

## Current Limitations

This API only creates temporary holds.

It does not implement:

- Final appointment booking
- Hold confirmation
- Appointment rescheduling
- Appointment cancellation
- Audit logs
- Email confirmation jobs

Final booking must still validate the hold and rely on PostgreSQL constraints.