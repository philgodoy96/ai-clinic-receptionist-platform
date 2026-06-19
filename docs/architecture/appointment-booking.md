# Appointment Booking Flow

## Context

Appointment booking transforms a temporary Redis hold into a durable PostgreSQL appointment.

The booking flow is intentionally separate from the hold flow.

A hold protects the user experience while the receptionist, chat agent, or Retell voice agent confirms details with the patient.

A booking creates the durable business record.

## Flow

1. Patient asks for an appointment.
2. Backend checks availability.
3. Patient selects a specific slot.
4. Backend creates a Redis hold.
5. Receptionist or AI agent confirms patient details.
6. Patient gives final confirmation.
7. Backend validates the hold.
8. Backend creates an appointment in PostgreSQL.
9. Backend marks the availability slot as booked.
10. Database transaction commits.
11. Backend releases the Redis hold.
12. Backend schedules confirmation email work.

## Current Implementation

The current implementation adds the appointment booking application service.

The service validates:

- Booking owner is present
- Patient exists
- Availability slot exists
- Availability slot is available
- Doctor exists and is active
- Redis hold exists
- Redis hold_id matches
- Redis owner matches
- No scheduled appointment already exists for the same doctor and start time

The service then:

- Creates a scheduled appointment
- Marks the availability slot as booked

## Transaction Boundary

The booking service does not commit database transactions.

The API or use-case boundary should commit the database transaction after the service prepares durable changes.

The Redis hold should be released only after the database commit succeeds.

This avoids a failure mode where the hold is released but the appointment is not saved.

## Redis and PostgreSQL Consistency

Redis and PostgreSQL do not participate in a single distributed transaction.

Redis protects the user experience by temporarily reserving a slot.

PostgreSQL remains the final consistency layer through durable records and database constraints.

The final booking API must still handle database conflict errors because two requests may race after passing application-level checks.

## Why Hold Release Happens After Commit

If the system releases the hold before the database commit, a commit failure could make the slot appear available again even though the booking attempt was not completed.

Correct order:

1. Validate hold
2. Create appointment
3. Mark slot booked
4. Commit PostgreSQL transaction
5. Release Redis hold

If releasing the Redis hold fails after commit, the durable appointment still exists.

The hold will eventually expire through Redis TTL.

## Current Limitations

This implementation phase does not expose a booking API endpoint yet.

It does not implement:

- Retell booking tool endpoint
- Patient creation during booking
- Audit logs
- RabbitMQ confirmation email jobs
- Database transaction wrapper in HTTP route
- Post-commit hold release handling

Those capabilities are planned for later implementation phases.