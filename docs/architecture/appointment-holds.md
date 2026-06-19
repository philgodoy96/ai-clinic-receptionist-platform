# Appointment Slot Holds

## Context

Appointment booking requires protection against two users selecting and confirming the same slot at nearly the same time.

The AI Clinic Receptionist Platform uses Redis for temporary appointment slot holds.

This is operational reservation state, not durable conversation memory.

## Flow

1. Chat or Retell checks availability.
2. User selects a slot.
3. Backend creates a temporary hold in Redis.
4. Backend returns `hold_id`.
5. User confirms.
6. Backend validates hold ownership and expiration.
7. Backend creates appointment in PostgreSQL.
8. Backend removes hold.
9. Backend enqueues confirmation email job.

The current implementation supports hold creation, validation, release, and API/tool exposure.

Final booking is implemented separately.

## Redis Key

The Redis key uses the doctor and slot start time:

    appointment_hold:{doctor_id}:{start_time}

The start time is normalized to UTC when timezone-aware.

## Hold Data

A hold stores:

- hold_id
- availability_slot_id
- doctor_id
- start_time
- end_time
- owner_id
- created_at

The `owner_id` represents the caller that owns the hold.

Examples:

- Retell call ID
- Chat conversation ID
- Future frontend session ID

## TTL

The default TTL is:

    300 seconds

That is equivalent to 5 minutes.

The value is configured through:

    APPOINTMENT_HOLD_TTL_SECONDS

## Important Invariants

- A slot can only have one active hold for a doctor/start_time pair.
- A hold must be validated by the same owner that created it.
- A mismatched hold_id cannot confirm a slot.
- An expired hold cannot be used for booking.
- Redis expiration releases abandoned holds automatically.
- PostgreSQL remains the final protection against double booking.

## Why Redis

Redis is appropriate because appointment holds are:

- Short-lived
- Operational
- Expiring
- Shared across API instances
- Not required as durable history

Durable appointment records still belong in PostgreSQL.

## What Redis Does Not Do

Redis does not replace PostgreSQL consistency.

Redis does not store durable appointment history.

Redis does not store long-term conversation memory.

Redis does not guarantee final booking correctness by itself.

The final booking flow must still validate availability and rely on PostgreSQL constraints.

## Current Limitations

This implementation does not yet include:

- Final appointment booking
- Appointment rescheduling
- Appointment cancellation
- Audit logs
- Email jobs
- Redis dependency health check

Those capabilities are planned for later implementation phases.