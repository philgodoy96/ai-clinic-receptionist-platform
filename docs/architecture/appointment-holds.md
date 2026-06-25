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

Hold creation, validation, release, booking, and cancellation are implemented.

## Redis Key

The Redis key uses the doctor and slot start time:

    appointment_hold:{doctor_id}:{start_time}

The start time is normalized to UTC when timezone-aware.

A secondary index key stores holds by `hold_id` for lookup and release.

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

### Backend-owned TTL policy

Appointment holds are intentionally short-lived and backend-owned. Redis holds protect against concurrent booking attempts during an active scheduling flow, but they are not durable reservations. If a call drops or a user abandons the flow, the hold expires automatically and the slot returns to availability. The backend owns the hold TTL policy so the voice agent cannot accidentally extend scheduling capacity locks.

- Retell voice tools do **not** control hold TTL. `expires_in_seconds` in hold responses reflects the configured backend value.
- Legacy `ttl_seconds` in Retell tool arguments, if present, is ignored.
- No hold recovery or hold renewal is implemented in the current voice scheduling flow.

For deployments with authenticated patient sessions, the system could introduce patient-aware hold recovery in a future phase. That would require additional identity and ownership rules and is intentionally kept separate from the current temporary coordination model.

## Availability interaction

When Redis is available, `check_availability` excludes slots that have an active hold for the same doctor and start time. Holds are checked in batch without mutating hold state from the read path.

When Redis hold lookup is unavailable, availability lookup may still return PostgreSQL-backed candidate slots (see **Redis Degradation Policy** below). A returned slot is not reserved until a hold succeeds.

## Important Invariants

- A slot can only have one active hold for a doctor/start_time pair.
- A hold must be validated by the same owner that created it.
- A mismatched hold_id cannot confirm a slot.
- An expired hold cannot be used for booking.
- Redis expiration releases abandoned holds automatically.
- PostgreSQL remains the final protection against double booking.
- Redis is required for hold creation; holds cannot be created when Redis is unavailable.

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

## Redis Degradation Policy

Redis is a temporary coordination layer, not the durable source of truth.

| Flow | Behavior when Redis is unavailable |
|------|-------------------------------------|
| Availability lookup (hold filtering) | Graceful degrade — DB-filtered slots may still be returned; internal warning logged |
| Hold creation | Fail closed — `appointment_hold_store_unavailable`; no hold claimed |
| Booking | Fail closed — requires valid hold in Redis |

Policy:

    Fail open for advisory availability reads.
    Fail closed for reservation, identity validation, booking, and cancellation safety boundaries.

Redis is **not optional** for holds or booking. Operators should treat Redis availability as required for reservation and booking flows.

See also: [Scheduling Application Services — Redis Degradation Policy](scheduling-services.md#redis-degradation-policy), [ADR-003](../adr/003-redis-appointment-holds.md).

## Current Limitations

Intentionally not implemented in the current voice scheduling flow:

- Patient-aware hold recovery after call drop or page refresh
- Hold renewal with a maximum absolute timeout
- Redis dependency health check surfaced on `/health/dependencies` beyond basic connectivity

Future work (separate from current coordination model and demo CLI generation):

- Dynamic doctor schedule engine and production rolling schedule rules
- Admin schedule management
- Background availability generation job

Demo operators use `python -m app.scripts.generate_demo_availability` for idempotent local/demo slot maintenance. See [Demo Availability Generation](../operations/demo-availability-generation.md).
