# Confirmation Email Jobs

## Context

After an appointment is confirmed, the platform should send a confirmation email to the patient.

The booking request should not send the email directly.

Instead, the request creates a durable email job that can be processed asynchronously by a worker.

## Current Implementation

The current implementation adds the durable job foundation.

It includes:

- Email job domain enums
- Email job SQLAlchemy model
- Email job migration
- Email job repository
- Email job service
- Confirmation job creation after booking
- Worker repository methods
- Fake email delivery provider
- Email job worker service
- Tests for confirmation email job creation and processing

The `email_jobs` table includes `locked_by` and `locked_until` fields to support future worker leasing and crash recovery.

## Flow

1. Patient confirms appointment.
2. Backend validates Redis hold.
3. Backend creates appointment.
4. Backend records booking audit event.
5. Backend creates pending confirmation email job.
6. PostgreSQL transaction commits.
7. Backend releases Redis hold.
8. Worker claims a pending email job.
9. Worker sends the email through a provider.
10. Worker marks the job as sent, failed, or dead_letter.

## Why the Job Is Created Before Commit

The email job is part of the durable booking side effect.

If booking rolls back, the email job should also roll back.

This prevents a worker from sending confirmation for an appointment that was not saved.

## Why Email Is Not Sent in the Request

Sending email inside the request would create several problems:

- Slow user response
- Provider timeout risk
- Harder retries
- Harder observability
- Inconsistent behavior if the request fails after sending

A durable background job is safer and more realistic.

## RabbitMQ Plan

This implementation does not publish to RabbitMQ yet.

A future implementation phase should add:

- RabbitMQ publisher
- RabbitMQ consumer
- Queue and DLQ configuration
- Message retry behavior
- Worker wake-up events

PostgreSQL should remain the source of truth for job state.

## Privacy Boundary

Email job payload should not contain:

- Diagnosis
- Clinical notes
- Raw transcripts
- Payment data
- Insurance identifiers

It may contain stable operational metadata such as:

- source
- hold_id
- call_id
- conversation_id

## Current Limitations

This implementation does not yet include:

- Real email provider
- RabbitMQ publisher
- RabbitMQ consumer
- Exponential backoff
- Job listing API
- Provider-level idempotency keys