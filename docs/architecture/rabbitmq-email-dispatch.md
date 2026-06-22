# RabbitMQ Email Dispatch

## Context

The platform creates durable email jobs after appointment booking.

RabbitMQ is used as a wake-up mechanism after a job is committed.

RabbitMQ is **not** the source of truth for job state.

PostgreSQL remains responsible for:

- job status
- `attempt_count` / `max_attempts`
- `locked_by` / `locked_until`
- `next_attempt_at`
- `sent_at`
- `last_error`
- `idempotency_key`
- `provider_message_id`

## Flow

1. Booking creates an appointment.
2. Booking creates an audit log.
3. Booking creates a pending email job.
4. PostgreSQL transaction commits.
5. The API attempts to publish an email job dispatch message to RabbitMQ.
6. If publish fails, the booking still succeeds because the job is durable.
7. RabbitMQ consumer receives the message.
8. Consumer invokes `EmailJobWorkerService.process_email_job()`.
9. Worker claims the job in Postgres and commits the claim.
10. Worker sends email through the provider outside the claim transaction.
11. Worker marks the job `sent`, retryable `pending`, or terminal `failed` in a second transaction.
12. Consumer acks the RabbitMQ message only after finalize state is durably committed.

## Message Shape

RabbitMQ dispatch messages contain only:

```json
{
  "email_job_id": "<uuid>"
}
```

The worker loads all delivery state from Postgres.

## Why Publish After Commit

Publishing before commit can wake a worker before the job exists durably.

The correct order is:

```
create appointment
create audit log
create email job
commit PostgreSQL transaction
publish RabbitMQ dispatch message
```

## Best-Effort Publishing

Publishing is best-effort.

If RabbitMQ is unavailable after the booking commits, the durable job still exists in PostgreSQL.

The polling worker (`scripts/run_email_job_worker.py`) can still process due jobs.

## Consumer Ack/Nack

Provider failures are **not** retried by RabbitMQ.

The worker persists retry or terminal `failed` state in Postgres (using `next_attempt_at`), commits that state, then the consumer **acks** the wake-up message.

If an unhandled database error occurs before finalize commit, the consumer **nacks with requeue** so Postgres remains authoritative and the broker can redeliver the wake-up.

Malformed payloads are **acked** without calling the provider so the consumer loop stays healthy.

## Retry Scheduling

Retry timing is controlled by `EmailJob.next_attempt_at` in Postgres.

RabbitMQ TTL retry queues are **not** used for provider send failures.

## Current Limitations

This implementation does not yet include:

- dedicated RabbitMQ DLQ exchange/queue for malformed messages
- RabbitMQ-based retry scheduling
- provider webhook/bounce handling
- delivery metrics dashboard

See [Email Dispatch Reliability](email-dispatch-reliability.md) for the full delivery guarantee model.
