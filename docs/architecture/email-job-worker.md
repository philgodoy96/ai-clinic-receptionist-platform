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

## RabbitMQ Boundary

RabbitMQ is not included in this implementation phase.

The current worker polls the durable PostgreSQL job table.

A future RabbitMQ integration may be used to wake workers or distribute processing events, but PostgreSQL remains the source of truth for job state.

## Idempotency Notes

The worker is designed for at-least-once execution.

A future implementation should add provider-level idempotency keys or a unique confirmation job constraint per appointment to reduce duplicate email risk.

## Current Limitations

This implementation does not yet include:

- Real email provider
- RabbitMQ publisher
- RabbitMQ consumer
- DLQ exchange/queue configuration
- Job listing API
- Provider idempotency keys
- Exponential backoff
- Metrics