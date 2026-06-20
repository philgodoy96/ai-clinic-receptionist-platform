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
10. Backend records booking audit event.
11. Backend creates pending confirmation email job.
12. Database transaction commits.
13. Backend releases the Redis hold.
14. Future worker processes the email job.

## Current Implementation

The current implementation includes:

- Appointment booking application service
- Generic booking API endpoint
- Retell booking tool endpoint
- Postgres commit at the API/tool boundary
- Redis hold release after successful commit
- Durable audit logs for hold and booking events
- Durable confirmation email jobs

## Transaction Boundary

The booking service does not commit database transactions.

The API or Retell adapter commits the database transaction after the service prepares durable changes.

The Redis hold is released only after the database commit succeeds.

This avoids a failure mode where the hold is released but the appointment is not saved.

## Durable Side Effects

Booking confirmation now creates a pending email job before the transaction commits.

This means appointment creation, audit logging, and email job creation can commit together.

If the booking transaction rolls back, the email job rolls back too.

## Redis and PostgreSQL Consistency

Redis and PostgreSQL do not participate in a single distributed transaction.

Redis protects the user experience by temporarily reserving a slot.

PostgreSQL remains the final consistency layer through durable records and database constraints.

The booking API still handles database conflict errors because two requests may race after passing application-level checks.

## Audit Logging

The booking flow records audit events for:

- Hold creation success
- Hold creation failure
- Booking confirmation success
- Booking confirmation failure

Audit logs are durable operational records stored in PostgreSQL.

They should not contain clinical notes or unnecessary patient details.

## Current Limitations

This implementation does not yet include:

- Patient creation during booking
- RabbitMQ confirmation email workers
- Real email provider integration
- Retell dashboard configuration
- Webhook signature validation
- Full concurrency simulation against PostgreSQL

Those capabilities are planned for later implementation phases.