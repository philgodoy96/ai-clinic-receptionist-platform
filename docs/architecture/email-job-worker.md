# Email Job Worker

## Context

Appointment booking creates a durable pending email job.

The scheduling API, Retell booking tool, and chat booking confirmation flow can all enqueue appointment confirmation email jobs after a booking is committed.

Immediate human escalations from chat can enqueue durable `human_escalation_notification` email jobs after escalation creation.

The email job worker processes those jobs asynchronously.

This keeps the booking request fast and avoids coupling user-facing latency to email provider availability.

## Delivery Guarantees

Email delivery uses **at-least-once processing**. Exactly-once delivery across Postgres and external providers is not guaranteed.

See [Email Dispatch Reliability](email-dispatch-reliability.md) for the full reliability model.

## Current Implementation

The current implementation includes:

- `OutboundEmailMessage` value object with optional `idempotency_key`
- `EmailDeliveryProvider` protocol
- `FakeEmailProvider` (default)
- optional `ResendEmailProvider` with idempotency keys
- `EmailJobWorkerService` with split claim/finalize transactions
- SQLAlchemy worker repository claim/lock methods
- polling CLI worker (`scripts/run_email_job_worker.py`)
- RabbitMQ dispatch consumer (`scripts/run_email_job_consumer.py`)
- retry policy with exponential backoff and `next_attempt_at`
- worker locking using `locked_by` and `locked_until`
- Email Job Debug API with cursor pagination
- manual retry/replay controls for `failed` jobs
- Email job operational metrics endpoint

## Supported Job Types

The worker currently handles these `job_type` values:

- `appointment_confirmation` — sends the stored confirmation subject/body through the delivery provider
- `human_escalation_notification` — renders a safe demo staff notification from operational payload fields, then sends through the provider

Unknown job types fail with the existing retry and terminal `failed` behavior.

Human escalation notification rendering includes escalation id, conversation id, reason, priority, source, summary, and selected handoff context such as active hold presence and selected doctor/date/time.

It does not include raw user messages, patient identity, raw LLM output, or raw prompts.

## Worker Flow

### Claim transaction

1. Worker claims the next available job (polling) or a specific job id (RabbitMQ wake-up).
2. Repository eligibility rules skip `sent`, terminal `failed`, future `next_attempt_at`, and active locks held by another worker.
3. Repository claims an eligible job by setting:
   - `status = processing`
   - `locked_by = worker_id`
   - `locked_until = now + lock_duration`
4. **Commit the claim transaction.**

### Provider call (outside database transaction)

5. Build the delivery message (including idempotency key when available).
6. Call `EmailDeliveryProvider.send()`.

### Finalize transaction

7. On success: mark `sent`, store `provider_message_id`, clear locks.
8. On failure: increment `attempt_count`, set `last_error`, return to `pending` with `next_attempt_at`, or mark `failed` when attempts are exhausted.
9. **Commit the finalize transaction.**

### RabbitMQ consumer

10. Acknowledge the RabbitMQ wake-up message only after finalize state is durably committed.

## RabbitMQ Boundary

RabbitMQ is used to wake workers.

PostgreSQL remains the source of truth for job state.

The RabbitMQ message contains only `email_job_id`. The worker still claims jobs from PostgreSQL to preserve durable locking and retry semantics.

## State Transitions

Supported transitions:

```
pending -> processing -> sent
pending -> processing -> pending   (retryable provider failure)
pending -> processing -> failed  (attempts exhausted)
failed  -> pending                 (manual retry/replay only)
```

`sent` jobs are not automatically retried.

## Crash Recovery

If a worker crashes while processing a job, the job may remain in `processing` with `locked_by` and `locked_until`.

Once `locked_until` expires, another worker can reclaim the job.

Because delivery is at-least-once, a crash after a successful provider send but before finalize commit can still produce a duplicate send on reclaim unless provider idempotency prevents it.

## Manual Recovery Controls

When automatic worker retries are not enough, operators can use the Email Job Debug API.

### Failed job retry

    POST /api/v1/email-jobs/{email_job_id}/retry

Retry applies only to jobs with status `failed`.

The service reuses the same job record:

- `status` becomes `pending`
- `next_attempt_at` is set to now
- `locked_by` and `locked_until` are cleared
- `attempt_count` and `last_error` are preserved

After the database commit, the API publishes a RabbitMQ wake message when dispatch is enabled.

### Failed job replay

    POST /api/v1/email-jobs/{email_job_id}/replay

Replay applies only to jobs with status `failed`.

The service resets the same job record for another delivery attempt:

- `status` becomes `pending`
- `next_attempt_at` is set to now
- `attempt_count` resets to `0`
- `last_error` is cleared
- lock fields are cleared

After the database commit, the API publishes a RabbitMQ wake message when dispatch is enabled.

## Operational Metrics

The Email Job Debug API exposes aggregate operational metrics at:

    GET /api/v1/email-jobs/metrics

`counts_by_status.failed` and `oldest_failed_created_at` help detect terminal delivery failures that need operator attention.

`overdue_pending_count` counts `pending` jobs whose `next_attempt_at` is due now or earlier.

`expired_lock_count` counts `processing` jobs whose lock has expired, which often indicates a worker crash before finalize.

See `docs/api/email-jobs.md` for the response shape.

## Idempotency

Appointment confirmation jobs use:

```
appointment_confirmation:{appointment_id}
```

When using Resend, the worker passes `EmailJob.idempotency_key` (or `email_job:{id}` fallback) as the Resend `Idempotency-Key` header.

Duplicate RabbitMQ wake-up messages for already `sent` jobs are ignored without calling the provider.

## Current Limitations

This implementation does not yet include:

- provider webhook/bounce handling
- dedicated RabbitMQ DLQ exchange/queue (malformed payloads are acked today)
- delivery metrics dashboard
- production alerting for terminal `failed` jobs
- Prometheus/Grafana integration for metrics export
