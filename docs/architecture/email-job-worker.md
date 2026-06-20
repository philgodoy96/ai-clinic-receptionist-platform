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
- Manual retry and dead-letter replay controls

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

## Manual Recovery Controls

When automatic worker retries are not enough, operators can use the Email Job Debug API to recover stuck or exhausted jobs.

### Failed job retry

    POST /api/v1/email-jobs/{email_job_id}/retry

Retry applies only to jobs with status `failed`.

The service reuses the same job record:

- status becomes `pending`
- scheduled_for is set to now
- locked_by and locked_until are cleared
- attempts and last_error are preserved

After the database commit, the API publishes a RabbitMQ wake message so a worker can claim the job again.

This is appropriate when a transient provider failure occurred and the job still has remaining attempts.

### Dead-letter replay

    POST /api/v1/email-jobs/{email_job_id}/replay

Replay applies only to jobs with status `dead_letter`.

The service creates a new pending job instead of mutating the original:

- the original dead_letter job remains unchanged for investigation
- the new job gets a new ID, attempts reset to 0, and last_error cleared
- replay metadata is added to the new job payload, including `replayed_from_email_job_id`

After the database commit, the API publishes a RabbitMQ wake message for the new job.

Replay creates a new job rather than mutating the original because dead_letter records represent the final exhausted state of a delivery attempt chain. Preserving that record keeps audit history intact and avoids overwriting failure context that may still be needed for root-cause analysis.

## Email Job Debug API

An Email Job Debug API now exists for local development and operator debugging.

Endpoints:

    GET /api/v1/email-jobs
    GET /api/v1/email-jobs/{email_job_id}
    POST /api/v1/email-jobs/{email_job_id}/retry
    POST /api/v1/email-jobs/{email_job_id}/replay

The list endpoint uses cursor pagination ordered by:

    created_at DESC, id DESC

The cursor contains the last returned job's timestamp and ID encoded as an opaque string.

Supported optional filters:

- job_type
- status
- appointment_id
- patient_id

See `docs/api/email-jobs.md` for request/response details.

This API does not yet include authentication or RBAC. Manual retry and dead-letter replay are available for local development and operator debugging.

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