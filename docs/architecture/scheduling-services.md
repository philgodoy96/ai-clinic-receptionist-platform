# Scheduling Application Services

## Context

Scheduling workflows require business validation before they are exposed through API routes, chat flows, or Retell tools.

The application service layer coordinates use cases and protects business invariants while keeping HTTP, Retell, and persistence details outside the core workflow logic.

## Decision

Scheduling use cases are implemented in `app/services/scheduling.py`.

The scheduling service depends on repository interfaces instead of SQLAlchemy queries directly.

This creates a clean boundary:

    API / Retell / Chat adapters
        -> application services
            -> repository interfaces
                -> SQLAlchemy repository adapters
                    -> database models

## Implemented Capabilities

The current scheduling service supports:

- Listing active specialties
- Listing active doctors
- Checking doctor availability with scheduling policy enforcement
- Looking up patients with sufficient identity
- Listing upcoming patient appointments
- Finding scheduled appointment conflicts
- Loading available slots for hold creation

Availability, hold, booking, and cancellation flows are exposed through HTTP routes, chat receptionist, and Retell tool adapters.

## Patient Identity Rule

Patient lookup requires more than full name and date of birth.

The service requires at least one additional identifier:

- Phone number
- Email

This reduces the risk of exposing patient data when two patients share similar names or dates of birth.

API and Retell tool layers preserve this rule.

## Scheduling Availability Policy

Availability lookup is a **candidate read model**, not a booking guarantee.

`check_availability` returns slots that are safe to offer as booking candidates. It does not reserve a slot and does not replace hold or booking validation.

### Policy window

Availability is filtered using clinic-local time (`ClinicTimeService`) and configurable policy:

| Setting | Default | Effect |
|---------|---------|--------|
| `SCHEDULING_MIN_BOOKING_LEAD_MINUTES` | `60` | Excludes past slots and slots starting before `clinic_now + lead` |
| `SCHEDULING_BOOKING_HORIZON_DAYS` | `14` | Excludes slots starting at or after `clinic_now + horizon` |

Requested query windows are clamped to this policy range before the database is queried.

### Excluded slots

A slot is not returned when:

- Its start time is in the past
- Its start time is inside the minimum booking lead time
- Its start time is beyond the configured booking horizon
- Its durable PostgreSQL status is not `available` (for example `booked`, `held`, `blocked`)
- An active Redis hold exists for the same doctor and start time (when Redis is available)

Expired Redis holds are not treated as active; TTL handles expiration.

### Consistency boundaries

Use this framing when reasoning about scheduling correctness:

    check_availability        = advisory read
    hold_appointment_slot     = temporary coordination boundary
    book_appointment          = durable consistency boundary
    reschedule_appointment    = durable state transition
    database constraints      = final safety net

- **Advisory read** — `check_availability` reflects current candidates but can be stale by the time a caller selects a time.
- **Temporary coordination** — `hold_appointment_slot` creates an exclusive Redis reservation for a short TTL. Redis is not the durable source of truth.
- **Durable consistency** — `book_appointment` validates an active hold, re-checks slot availability in PostgreSQL, and creates the appointment.
- **Durable reschedule** — `reschedule_appointment` validates identity, original appointment eligibility, hold validity, and new slot availability, then performs an atomic historical-preserving transition:

```text
old appointment -> rescheduled
old slot -> available
new appointment -> scheduled
new slot -> booked
```

If rescheduling fails, no partial durable state should remain at the service boundary.

- **Final safety net** — PostgreSQL constraints and slot status transitions protect against duplicate scheduled appointments even if earlier layers race.

Postgres remains the durable source of truth for slot status and appointments. Redis holds are ephemeral coordination state.

See also: [Configuration — Scheduling Availability Policy](../configuration.md#scheduling-availability-policy), [Appointment Slot Holds](appointment-holds.md), [Appointment Booking](appointment-booking.md).

## Redis Degradation Policy

Redis is used for temporary holds and coordination. It is **not** the durable source of truth.

Degradation behavior is **operation-specific**, not global:

| Operation | Redis role | When Redis is unavailable |
|-----------|------------|---------------------------|
| `check_availability` | Filter out actively held slots | **Fail open (graceful degrade)** — return DB-filtered candidate slots; log internally; do not expose Redis details to callers |
| `hold_appointment_slot` | Create exclusive hold | **Fail closed** — return `appointment_hold_store_unavailable`; do not claim the time is held |
| `book_appointment` | Validate hold before booking | **Fail closed** — booking requires a valid hold |
| Patient identity resolution (Redis-backed) | Short-lived resolution tokens | **Fail closed** when Redis is required for correctness |

Policy summary:

    Fail open for advisory availability reads.
    Fail closed for reservation, identity validation, booking, and cancellation safety boundaries.

Fail-open applies only to Redis hold **filtering** during availability lookup. It does not apply to holds, booking, or other state-changing flows.

Manual validation (June 2026): with Redis stopped, availability could still return DB-backed slots; hold attempts returned `appointment_hold_store_unavailable` and the assistant did not claim the time was held. With Redis restored, hold creation succeeded again.

## Availability Rule

Availability lookup requires:

- Existing doctor
- Active doctor
- Valid time window where `start_to > start_from`

The service rejects invalid windows before calling the availability repository.

Retell and chat paths may apply additional clinic-time resolution (business days, business hours) before calling `SchedulingService.check_availability`.

## Transaction Boundary

The scheduling service does not commit transactions.

Write workflows keep transaction ownership at the application use case or request boundary.

Booking coordinates several steps:

1. Patient validation
2. Doctor validation
3. Availability validation
4. Redis appointment hold validation
5. Appointment creation
6. Audit log creation
7. Email job scheduling

Durable database changes, such as appointment creation and audit log records, should be committed atomically.

External or operational steps, such as Redis hold validation and RabbitMQ email delivery, should not be treated as part of the same database transaction. They should be coordinated through clear ordering, idempotency, retries, and eventually an outbox-style pattern if stronger reliability is needed.

## Cancellation and slot release

When an appointment is cancelled, the linked availability slot is released back to `available` in PostgreSQL. That slot may appear again in availability if it falls within the lead-time and horizon policy window and is not Redis-held.

## Current Limitations

Intentional future evolution (not missing MVP requirements):

- Written chat reschedule flow
- Patient-aware hold recovery for authenticated patient sessions
- Hold renewal with maximum absolute timeout
- Dynamic doctor schedule rules
- Rolling availability generation beyond seeded demo slots
- Admin schedule management UI
- Rescheduling and cancellation email notification templates where not yet deployed

## Testing Strategy

Service tests use fake repositories.

This keeps tests fast and focused on application behavior rather than persistence details.

Repository tests separately validate SQLAlchemy query behavior.

Availability hardening scenarios are covered in `tests/test_scheduling_availability_hardening.py`.
