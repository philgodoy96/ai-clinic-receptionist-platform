# ADR-003: Redis is used for appointment holds, not durable conversation memory

## Status

Accepted

## Context

Appointment booking requires a temporary reservation mechanism.

When a patient selects an available slot, the system needs to prevent another user from holding the same doctor/time slot during the confirmation window.

This state should expire automatically if the patient does not confirm.

A tempting design would be to store this in PostgreSQL immediately as a pending appointment.

Another tempting design would be to treat Redis as general conversation memory.

Both designs create problems.

PostgreSQL should store durable business records.

Redis should store short-lived operational state.

## Decision

Redis is used for temporary appointment slot holds.

Redis is not used as durable conversation memory.

The hold key is based on:

    appointment_hold:{doctor_id}:{start_time}

The hold value stores:

- hold_id
- availability_slot_id
- doctor_id
- start_time
- end_time
- owner_id
- created_at

The hold expires automatically using a TTL.

The default TTL is 5 minutes.

## Consequences

### Positive Consequences

The system can prevent two users from holding the same slot at the same time.

Abandoned holds expire automatically.

The hold mechanism can be shared across multiple API instances.

The backend does not need cleanup jobs for normal abandoned holds.

Redis remains focused on short-lived operational state.

### Negative Consequences

Redis is not durable storage.

A Redis outage can prevent new holds from being created.

A Redis restart may lose active holds depending on deployment configuration.

The final booking flow must still validate availability and rely on PostgreSQL constraints.

## Alternatives Considered

### Alternative 1: Store pending appointments in PostgreSQL

This was rejected for V1.

Pending appointment rows can work, but they require cleanup logic, state transitions, and more durable workflow complexity.

Redis TTL gives simpler temporary reservation semantics.

### Alternative 2: Store holds only in application memory

This was rejected.

Application memory does not work across multiple API instances and loses state on process restart.

### Alternative 3: Skip holds and rely only on final database constraint

This was rejected.

Database constraints are still required, but without holds the user experience is worse because multiple users may be offered and attempt to confirm the same slot.

## Implementation Guidance

Redis hold creation should use atomic set-if-not-exists behavior.

Hold validation must check:

- hold exists
- hold_id matches
- owner_id matches
- slot has not expired

Booking must still rely on PostgreSQL constraints as the final consistency layer.

Redis holds should not contain medical diagnosis, clinical notes, or unnecessary patient data.

## Redis Degradation Policy

Availability lookup may degrade gracefully when Redis hold filtering fails: PostgreSQL-backed candidate slots can still be returned without exposing internal Redis errors to callers.

Reservation and booking flows fail closed when Redis is required:

- Hold creation returns `appointment_hold_store_unavailable` when Redis cannot store the hold.
- Booking requires a valid hold; it cannot proceed without Redis coordination.

This is intentional partial degradation, not global fail-open behavior:

    Fail open for advisory availability reads.
    Fail closed for reservation, identity validation, booking, and cancellation safety boundaries.

Postgres slot status and database constraints remain the durable source of truth and final safety net.

## Related ADRs

- ADR-001: Voice UX must not be modeled as chat UX
- ADR-002: Retell is an adapter, not the business logic core
- ADR-004: Background email confirmation uses RabbitMQ
- ADR-005: Backend enforces guardrails, provider prompt only guides behavior