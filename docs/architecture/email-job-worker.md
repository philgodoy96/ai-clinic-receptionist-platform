# Email Job Worker

## Context

Appointment booking creates a durable pending email job.

The email job worker processes those jobs asynchronously.

This keeps the booking request fast and avoids coupling user-facing latency to email provider availability.

## Current Implementation

The current implementation includes:

- EmailMessage value object
- EmailDeliveryProvider protocol
- FakeEmailDeliveryProvider
- Email job worker repository methods
- EmailJobWorkerService
- CLI worker script
- Retry handling
- Dead-letter state transition
- Worker locking using locked_by and locked_until
- RabbitMQ dispatch consumer
- Email Job Debug API with cursor pagination

## Worker Flow

1. Worker asks the repository for the next available job.
2. Repository finds pending or failed jobs scheduled for now or earlier.
3. Repository skips jobs locked by another worker until locked_until expires.
4. Repository claims a job by setting:
   - status = processing
   - locked_by = worker_id
   - locked_until = now + lock_duration
   - attempts = attempts + 1
5. Worker sends email using the provider.
6. On success, repository marks the job as sent.
7. On failure, repository marks the job as failed or dead_letter.
8. Locks are cleared after success or failure.

## RabbitMQ Boundary

RabbitMQ is used to wake/distribute workers.

PostgreSQL remains the source of truth for job state.

The RabbitMQ message contains the email_job_id for observability, but the worker still claims jobs from PostgreSQL to preserve durable locking and retry semantics.

## State Transitions

Supported transitions:

    pending -> processing -> sent
    pending -> processing -> failed
    failed -> processing -> sent
    failed -> processing -> failed
    failed -> processing -> dead_letter

## Crash Recovery

If a worker crashes while processing a job, the job may remain in processing with locked_by and locked_until.

Once locked_until expires, another worker can claim the job.

This makes the job recoverable without requiring manual cleanup.

## Email Job Debug API

An Email Job Debug API now exists for local development and operator debugging.

Endpoints:

    GET /api/v1/email-jobs
    GET /api/v1/email-jobs/{email_job_id}

The list endpoint uses cursor pagination ordered by:

    created_at DESC, id DESC

The cursor contains the last returned job's timestamp and ID encoded as an opaque string.

Supported optional filters:

- job_type
- status
- appointment_id
- patient_id

See `docs/api/email-jobs.md` for request/response details.

This API does not yet include authentication, manual retry, or dead-letter replay.

## Idempotency Notes

The worker is designed for at-least-once execution.

A future implementation should add provider-level idempotency keys or a unique confirmation job constraint per appointment to reduce duplicate email risk.

## Current Limitations

This implementation does not yet include:

- Real email provider
- DLQ exchange/queue configuration
- Provider idempotency keys
- Exponential backoff
- Metrics